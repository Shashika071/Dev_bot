"""
Deriv trading WebSocket client — authorize / proposal / buy only.

Isolated from the public market-data client (which still forbids buy/sell).
Use only when auto-trade is explicitly enabled and a trade token is configured.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

import structlog
import websockets

from app.config import settings

logger = structlog.get_logger(__name__)

ALLOWED_ROOT_KEYS = {
    "authorize",
    "proposal",
    "buy",
    "ping",
    "balance",
    "profit_table",
    "get_account_status",
}


class DerivTradeClient:
    """
    Short-lived authenticated WS for placing One-Touch contracts.

    Uses classic v3 account WebSocket (authorize/balance/proposal/buy) per
    https://developers.deriv.com/docs/ — not the public market-data endpoint.
    """

    # Cloudflare 520s are common on a single edge — rotate hosts + retry.
    _HOSTS = (
        "ws.derivws.com",
        "green.derivws.com",
        "blue.derivws.com",
        "ws.binaryws.com",
    )

    def __init__(self, token: str, app_id: str | int | None = None):
        self.token = str(token).strip()
        # Prefer configured app_id; 1089 is Deriv's documented public sample app_id
        self.app_id = str(app_id or settings.deriv_app_id or "1089")
        self.ws: Optional[Any] = None
        self._connected_url: Optional[str] = None
        self._req_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._reader_task: Optional[asyncio.Task] = None
        self._authorized_loginid: Optional[str] = None

    def _candidate_urls(self) -> list[str]:
        app_ids = [self.app_id]
        # If custom/missing app_id fails InvalidAppID, also try common public IDs
        for extra in ("1089", "36544"):
            if extra not in app_ids:
                app_ids.append(extra)
        urls: list[str] = []
        for host in self._HOSTS:
            for aid in app_ids:
                urls.append(f"wss://{host}/websockets/v3?app_id={aid}")
        return urls

    def _next_req_id(self) -> int:
        self._req_id += 1
        return self._req_id

    async def connect(self) -> None:
        if self.ws is not None:
            return
        errors: list[str] = []
        for url in self._candidate_urls():
            for attempt in range(1, 3):
                try:
                    self.ws = await websockets.connect(
                        url,
                        open_timeout=20,
                        ping_interval=20,
                        ping_timeout=20,
                        close_timeout=5,
                        origin="https://app.deriv.com",
                        user_agent_header="deriv-touch-bot/1.0",
                    )
                    self._connected_url = url
                    self._reader_task = asyncio.create_task(self._read_loop())
                    # Keep app_id that worked (from query)
                    if "app_id=" in url:
                        self.app_id = url.split("app_id=")[-1].split("&")[0]
                    logger.info("deriv_trade_ws_connected", url=url, attempt=attempt)
                    return
                except Exception as e:
                    msg = str(e)
                    errors.append(f"{url} attempt{attempt}: {msg}")
                    logger.warning(
                        "deriv_trade_ws_connect_failed",
                        url=url,
                        attempt=attempt,
                        error=msg,
                    )
                    self.ws = None
                    # Brief backoff on Cloudflare 520 / transient gateway errors
                    if "520" in msg or "502" in msg or "503" in msg:
                        await asyncio.sleep(0.6 * attempt)
                    else:
                        break  # try next URL
        raise ConnectionError(
            "Could not open Deriv trade WebSocket (authorize endpoint). "
            "HTTP 520 = Deriv/Cloudflare edge issue or blocked path — retried hosts. "
            "Ensure token is valid and DERIV_APP_ID is set in .env.prod if you have a registered app. "
            f"Last errors: {'; '.join(errors[-3:])}"
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
        data = await self.send({"balance": 1, "account": "current"})
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

    async def fetch_account_summary(self) -> dict[str, Any]:
        """
        Authorize + balance + recent profit_table.
        Returns loginid, currency, balance, is_virtual, today/total profit, etc.
        """
        auth = await self.authorize()
        bal = {}
        try:
            bal = await self.get_balance()
        except Exception as e:
            logger.warning("deriv_balance_failed", error=str(e))

        currency = (
            bal.get("currency")
            or auth.get("currency")
            or "USD"
        )
        balance = float(bal.get("balance") if bal.get("balance") is not None else auth.get("balance") or 0)

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
                # purchase_time is epoch seconds
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
            "country": auth.get("country"),
        }

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
        data = await self.send(
            {
                "proposal": 1,
                "amount": float(amount),
                "basis": basis,
                "contract_type": contract_type,
                "currency": currency,
                "duration": int(duration),
                "duration_unit": duration_unit,
                "symbol": symbol,
                "barrier": barrier,
            }
        )
        return data.get("proposal") or {}

    async def buy(self, proposal_id: str, price: float) -> dict:
        data = await self.send(
            {
                "buy": str(proposal_id),
                "price": float(price),
            }
        )
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
        """
        Authorize (if needed), request proposal, buy One-Touch.
        Returns {ok, contract_id, buy_price, longcode, error, raw}.
        """
        try:
            if not self._authorized_loginid:
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
            ask = float(proposal.get("ask_price") or 0)
            if not pid or ask <= 0:
                return {
                    "ok": False,
                    "error": "Empty proposal from Deriv",
                    "raw": proposal,
                }
            # Price buffer slightly above ask (Deriv accepts max price)
            buy_price = round(ask * 1.02, 2)
            bought = await self.buy(pid, buy_price)
            contract_id = bought.get("contract_id")
            return {
                "ok": True,
                "contract_id": contract_id,
                "buy_price": bought.get("buy_price") or ask,
                "longcode": bought.get("longcode") or proposal.get("longcode"),
                "loginid": self._authorized_loginid,
                "direction": direction,
                "barrier": barrier,
                "stake": stake,
                "raw": bought,
            }
        except Exception as e:
            logger.error("deriv_place_one_touch_failed", error=str(e))
            return {"ok": False, "error": str(e)}
