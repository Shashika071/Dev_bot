"""
Deriv trading client — supports legacy API tokens and new PAT apps.

New PAT flow (developers.deriv.com):
  REST: Authorization: Bearer <pat_...> + Deriv-App-ID
  GET /trading/v1/options/accounts  → balance / account list
  POST .../accounts/{id}/otp         → authenticated WebSocket URL
  WS: proposal / buy on that URL

Legacy flow (classic tokens):
  wss://ws.derivws.com/websockets/v3?app_id=...
  authorize → proposal → buy
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

import httpx
import structlog
import websockets

from app.config import settings

logger = structlog.get_logger(__name__)

API_BASE = "https://api.derivws.com"

ALLOWED_ROOT_KEYS = {
    "authorize",
    "proposal",
    "buy",
    "ping",
    "balance",
    "profit_table",
    "get_account_status",
}


def _is_pat(token: str) -> bool:
    t = (token or "").strip().lower()
    return t.startswith("pat_")


class DerivTradeClient:
    """Short-lived client for account lookup + One-Touch buy."""

    _HOSTS = ("ws.derivws.com", "green.derivws.com")

    def __init__(self, token: str, app_id: str | int | None = None):
        self.token = str(token).strip()
        # PAT apps: use App ID from Setup / .env — never fall back to legacy 1089
        if app_id is None or str(app_id).strip() == "":
            try:
                from app.trade_prefs import resolve_deriv_app_id

                app_id = resolve_deriv_app_id()
            except Exception:
                app_id = settings.deriv_app_id
        self.app_id = str(app_id or "").strip()
        self.ws: Optional[Any] = None
        self._connected_url: Optional[str] = None
        self._req_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._reader_task: Optional[asyncio.Task] = None
        self._authorized_loginid: Optional[str] = None
        self._account_id: Optional[str] = None
        self._use_pat = _is_pat(self.token)
        if self._use_pat and not self.app_id:
            raise RuntimeError(
                "PAT token requires App ID. Set Setup → Deriv App ID "
                "or DERIV_APP_ID in .env.prod (from developers.deriv.com → Apps)."
            )

    def _rest_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Deriv-App-ID": self.app_id,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _next_req_id(self) -> int:
        self._req_id += 1
        return self._req_id

    async def list_accounts(self) -> list[dict]:
        """GET options accounts (PAT / new API)."""
        if not self.app_id:
            raise RuntimeError("Missing Deriv App ID for PAT request")
        async with httpx.AsyncClient(timeout=20.0) as http:
            r = await http.get(
                f"{API_BASE}/trading/v1/options/accounts",
                headers=self._rest_headers(),
            )
            if r.status_code >= 400:
                detail = r.text[:300]
                if r.status_code == 401 and "application" in detail.lower():
                    raise RuntimeError(
                        f"Invalid application (App ID mismatch). "
                        f"Using App ID={self.app_id!r}. "
                        f"Copy exact App ID from developers.deriv.com → Apps "
                        f"and save it in Setup or DERIV_APP_ID. Raw: {detail}"
                    )
                raise RuntimeError(
                    f"Accounts API HTTP {r.status_code}: {detail}"
                )
            body = r.json()
        data = body.get("data") if isinstance(body, dict) else body
        if isinstance(data, dict) and "accounts" in data:
            data = data["accounts"]
        if not isinstance(data, list):
            raise RuntimeError(f"Unexpected accounts response: {str(body)[:200]}")
        return data

    async def request_otp_ws_url(self, account_id: str) -> str:
        async with httpx.AsyncClient(timeout=20.0) as http:
            r = await http.post(
                f"{API_BASE}/trading/v1/options/accounts/{account_id}/otp",
                headers=self._rest_headers(),
            )
            if r.status_code >= 400:
                raise RuntimeError(f"OTP API HTTP {r.status_code}: {r.text[:300]}")
            body = r.json()
        data = body.get("data") if isinstance(body, dict) else {}
        url = (data or {}).get("url") or (body.get("url") if isinstance(body, dict) else None)
        if not url:
            raise RuntimeError(f"OTP response missing url: {str(body)[:200]}")
        return str(url)

    def _pick_account(self, accounts: list[dict]) -> dict:
        if not accounts:
            raise RuntimeError("No Deriv options accounts on this PAT")
        # Prefer demo first (safer), else first active
        for a in accounts:
            at = str(a.get("account_type") or a.get("group") or "").lower()
            if "demo" in at or str(a.get("account_id") or "").upper().startswith(("VRTC", "VRT", "DOT")):
                return a
        return accounts[0]

    async def fetch_account_summary(self) -> dict[str, Any]:
        """Balance / account details for Setup UI."""
        if self._use_pat:
            return await self._fetch_summary_pat()
        return await self._fetch_summary_legacy()

    async def _fetch_summary_pat(self) -> dict[str, Any]:
        accounts = await self.list_accounts()
        chosen = self._pick_account(accounts)
        self._account_id = str(
            chosen.get("account_id") or chosen.get("loginid") or chosen.get("id") or ""
        )
        try:
            balance = float(chosen.get("balance") or 0)
        except (TypeError, ValueError):
            balance = 0.0
        currency = str(chosen.get("currency") or "USD")
        at = str(chosen.get("account_type") or chosen.get("group") or "").lower()
        is_virtual = "demo" in at or self._account_id.upper().startswith(("VRTC", "VRT", "DOT"))
        # Enrich via OTP WS balance if REST balance missing
        if balance <= 0:
            try:
                await self.connect_pat(self._account_id)
                bal = await self.get_balance()
                balance = float(bal.get("balance") or 0)
                currency = str(bal.get("currency") or currency)
            except Exception as e:
                logger.warning("pat_balance_ws_failed", error=str(e))

        return {
            "ok": True,
            "loginid": self._account_id,
            "currency": currency,
            "balance": balance,
            "is_virtual": is_virtual,
            "account_type": "demo" if is_virtual else "real",
            "email": None,
            "fullname": None,
            "today_profit": None,
            "recent_profit": None,
            "recent_trades": None,
            "recent_wins": None,
            "recent_losses": None,
            "accounts": [
                {
                    "account_id": a.get("account_id") or a.get("loginid"),
                    "balance": a.get("balance"),
                    "currency": a.get("currency"),
                    "account_type": a.get("account_type") or a.get("group"),
                }
                for a in accounts
            ],
            "auth_mode": "pat",
            "app_id": self.app_id,
        }

    async def _fetch_summary_legacy(self) -> dict[str, Any]:
        auth = await self.authorize()
        bal = {}
        try:
            bal = await self.get_balance()
        except Exception as e:
            logger.warning("deriv_balance_failed", error=str(e))

        currency = bal.get("currency") or auth.get("currency") or "USD"
        balance = float(
            bal.get("balance") if bal.get("balance") is not None else auth.get("balance") or 0
        )

        today_profit = 0.0
        total_profit = 0.0
        trade_count = 0
        wins = 0
        losses = 0
        try:
            import datetime as _dt

            pt = await self.get_profit_table(limit=100)
            transactions = pt.get("transactions") or []
            today = _dt.datetime.now(_dt.timezone.utc).date()
            for tx in transactions:
                try:
                    profit = float(tx.get("profit") or 0)
                except (TypeError, ValueError):
                    continue
                total_profit += profit
                trade_count += 1
                if profit > 0:
                    wins += 1
                elif profit < 0:
                    losses += 1
                ts = tx.get("purchase_time") or tx.get("transaction_time")
                if ts is not None:
                    try:
                        d = _dt.datetime.fromtimestamp(float(ts), tz=_dt.timezone.utc).date()
                        if d == today:
                            today_profit += profit
                    except Exception:
                        pass
        except Exception as e:
            logger.warning("deriv_profit_table_failed", error=str(e))

        return {
            "ok": True,
            "loginid": auth.get("loginid") or self._authorized_loginid,
            "currency": currency,
            "balance": balance,
            "is_virtual": bool(auth.get("is_virtual")),
            "email": auth.get("email"),
            "fullname": auth.get("fullname"),
            "account_type": "demo" if auth.get("is_virtual") else "real",
            "today_profit": round(today_profit, 2),
            "recent_profit": round(total_profit, 2),
            "recent_trades": trade_count,
            "recent_wins": wins,
            "recent_losses": losses,
            "auth_mode": "legacy",
            "app_id": self.app_id,
        }

    async def connect(self) -> None:
        if self.ws is not None:
            return
        if self._use_pat:
            if not self._account_id:
                accounts = await self.list_accounts()
                chosen = self._pick_account(accounts)
                self._account_id = str(
                    chosen.get("account_id") or chosen.get("loginid") or ""
                )
            await self.connect_pat(self._account_id)
            return
        await self._connect_legacy()

    async def connect_pat(self, account_id: str) -> None:
        if self.ws is not None:
            await self.close()
        url = await self.request_otp_ws_url(account_id)
        self.ws = await websockets.connect(
            url,
            open_timeout=15,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=3,
            user_agent_header="deriv-touch-bot/1.0",
        )
        self._connected_url = url.split("?")[0]
        self._account_id = account_id
        self._authorized_loginid = account_id
        self._reader_task = asyncio.create_task(self._read_loop())
        logger.info("deriv_trade_pat_ws_connected", account_id=account_id)

    async def _connect_legacy(self) -> None:
        errors: list[str] = []
        deadline = asyncio.get_event_loop().time() + 18.0
        app_ids = [self.app_id]
        if "1089" not in app_ids:
            app_ids.append("1089")
        urls = [
            f"wss://{host}/websockets/v3?app_id={aid}"
            for host in self._HOSTS
            for aid in app_ids
        ]
        for url in urls:
            if asyncio.get_event_loop().time() >= deadline:
                break
            try:
                self.ws = await websockets.connect(
                    url,
                    open_timeout=5,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=3,
                    origin="https://app.deriv.com",
                    user_agent_header="deriv-touch-bot/1.0",
                )
                self._connected_url = url
                self._reader_task = asyncio.create_task(self._read_loop())
                if "app_id=" in url:
                    self.app_id = url.split("app_id=")[-1].split("&")[0]
                logger.info("deriv_trade_ws_connected", url=url)
                return
            except Exception as e:
                errors.append(f"{url}: {e}")
                self.ws = None
                await asyncio.sleep(0.2)
        raise ConnectionError(
            "Could not open legacy Deriv trade WebSocket. "
            f"Last: {errors[-1] if errors else 'no attempts'}"
        )

    async def close(self) -> None:
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None
        if self.ws is not None:
            try:
                await self.ws.close()
            except Exception:
                pass
            self.ws = None
        for fut in self._pending.values():
            if not fut.done():
                fut.cancel()
        self._pending.clear()

    async def _read_loop(self) -> None:
        assert self.ws is not None
        try:
            async for raw in self.ws:
                try:
                    data = json.loads(raw)
                except Exception:
                    continue
                req_id = data.get("req_id")
                if req_id is not None and int(req_id) in self._pending:
                    fut = self._pending.pop(int(req_id))
                    if not fut.done():
                        fut.set_result(data)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("deriv_trade_ws_read_error", error=str(e))
            for fut in list(self._pending.values()):
                if not fut.done():
                    fut.set_exception(ConnectionError(str(e)))
            self._pending.clear()

    async def send(self, msg: dict, timeout: float = 30.0) -> dict:
        root = next((k for k in msg if k not in ("req_id", "passthrough")), None)
        if root not in ALLOWED_ROOT_KEYS:
            raise RuntimeError(f"Trade client blocked message type: {root}")
        if self.ws is None:
            await self.connect()
        assert self.ws is not None
        req_id = self._next_req_id()
        payload = dict(msg)
        payload["req_id"] = req_id
        fut = asyncio.get_event_loop().create_future()
        self._pending[req_id] = fut
        await self.ws.send(json.dumps(payload))
        try:
            data = await asyncio.wait_for(fut, timeout=timeout)
        except Exception:
            self._pending.pop(req_id, None)
            raise
        if data.get("error"):
            err = data["error"]
            raise RuntimeError(err.get("message") or str(err))
        return data

    async def authorize(self) -> dict:
        data = await self.send({"authorize": self.token})
        auth = data.get("authorize") or {}
        self._authorized_loginid = auth.get("loginid")
        logger.info(
            "deriv_trade_authorized",
            loginid=self._authorized_loginid,
            currency=auth.get("currency"),
            is_virtual=auth.get("is_virtual"),
        )
        return auth

    async def get_balance(self) -> dict:
        data = await self.send({"balance": 1})
        return data.get("balance") or {}

    async def get_profit_table(self, *, limit: int = 100) -> dict:
        data = await self.send(
            {
                "profit_table": 1,
                "description": 1,
                "limit": int(limit),
                "offset": 0,
                "sort": "DESC",
            }
        )
        return data.get("profit_table") or {}

    async def get_proposal(
        self,
        *,
        symbol: str,
        contract_type: str,
        duration: int,
        duration_unit: str,
        barrier: str,
        amount: float,
        currency: str = "USD",
        basis: str = "stake",
    ) -> dict:
        # New API: symbol → underlying_symbol only (sending both → "Properties not allowed: symbol")
        msg: dict[str, Any] = {
            "proposal": 1,
            "amount": float(amount),
            "basis": basis,
            "contract_type": contract_type,
            "currency": currency,
            "duration": int(duration),
            "duration_unit": duration_unit,
            "barrier": str(barrier),
        }
        if self._use_pat:
            msg["underlying_symbol"] = symbol
        else:
            msg["symbol"] = symbol
        data = await self.send(msg)
        return data.get("proposal") or {}

    async def buy(self, proposal_id: str, price: float) -> dict:
        data = await self.send({"buy": str(proposal_id), "price": float(price)})
        return data.get("buy") or data

    async def place_one_touch(
        self,
        *,
        symbol: str,
        direction: str,
        barrier: str,
        duration: int,
        duration_unit: str,
        stake: float,
        currency: str = "USD",
    ) -> dict[str, Any]:
        try:
            await self.connect()
            if not self._use_pat and not self._authorized_loginid:
                await self.authorize()
            proposal = await self.get_proposal(
                symbol=symbol,
                contract_type="ONETOUCH",
                duration=duration,
                duration_unit=duration_unit,
                barrier=barrier,
                amount=stake,
                currency=currency,
                basis="stake",
            )
            pid = proposal.get("id")
            try:
                ask = float(proposal.get("ask_price") or 0)
            except (TypeError, ValueError):
                ask = 0.0
            if not pid or ask <= 0:
                return {"ok": False, "error": "Empty proposal from Deriv", "raw": proposal}
            buy_price = round(ask * 1.02, 2)
            bought = await self.buy(pid, buy_price)
            try:
                buy_price_out = float(bought.get("buy_price") or ask)
            except (TypeError, ValueError):
                buy_price_out = ask
            return {
                "ok": True,
                "contract_id": bought.get("contract_id"),
                "buy_price": buy_price_out,
                "longcode": bought.get("longcode") or proposal.get("longcode"),
                "loginid": self._authorized_loginid or self._account_id,
                "direction": direction,
                "barrier": barrier,
                "stake": stake,
                "auth_mode": "pat" if self._use_pat else "legacy",
                "raw": bought,
            }
        except Exception as e:
            logger.error("deriv_place_one_touch_failed", error=str(e))
            return {"ok": False, "error": str(e)}
