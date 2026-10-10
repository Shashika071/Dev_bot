"""Digit Matches session on the same Deriv login the touch bot already saved.

PAT tokens use the touch bot's OTP trading socket.
Classic tokens use the touch bot's legacy v3 socket.
Market requests keep the schema that matches that login.
"""

from __future__ import annotations

import asyncio
import json

from app.deriv.trade_client import ALLOWED_ROOT_KEYS, DerivTradeClient
from app.digitmatch.account import AccountRejected
from app.digitmatch.errors import PurchaseNotSent, PurchaseOutcomeUnknown, PurchaseRejected
from app.digitmatch.messages import (
    error_of,
    parse_buy,
    parse_history_ticks,
    parse_open_contract,
    parse_proposal,
    parse_tick,
    proposal_request,
)

_EXTRA_KEYS = ALLOWED_ROOT_KEYS | {
    "active_symbols",
    "contracts_for",
    "ticks_history",
    "ticks",
    "statement",
    "proposal_open_contract",
    "forget",
    "ping",
}


class DigitMatchSession(DerivTradeClient):
    """Demo-only session. A real account is never selected."""

    def __init__(self, token: str, app_id: str | None):
        super().__init__(token, app_id=app_id or None)
        self._demo_account: dict | None = None
        self._tick_handler = None

    @property
    def connected(self) -> bool:
        return self.ws is not None

    async def connect(self) -> None:
        if self.ws is not None:
            return
        if self._use_pat:
            if not self.app_id:
                raise RuntimeError(
                    "Save the Deriv App ID in Configuration. "
                    "Digit Matches reuses that same App ID."
                )
            accounts = await self.list_accounts()
            demos = [row for row in accounts if self._is_demo_account(row)]
            if not demos:
                raise AccountRejected("real account rejected")
            chosen = demos[0]
            if "is_virtual" in chosen:
                try:
                    if int(chosen.get("is_virtual")) != 1:
                        raise AccountRejected("real account rejected")
                except AccountRejected:
                    raise
                except (TypeError, ValueError):
                    raise AccountRejected("demo status cannot be verified")
            self._demo_account = chosen
            account_id = str(chosen.get("account_id") or chosen.get("loginid") or "")
            await self.connect_pat(account_id)
            return
        if not self.app_id:
            raise RuntimeError(
                "Save the Deriv App ID in Configuration. "
                "Digit Matches reuses that same App ID."
            )
        await self._connect_legacy()

    async def authorize(self) -> dict:
        if self._use_pat:
            account = self._demo_account or {}
            if not self._is_demo_account(account):
                raise AccountRejected("real account rejected")
            loginid = str(account.get("loginid") or account.get("account_id") or self._account_id or "")
            if not loginid:
                raise AccountRejected("demo status cannot be verified")
            return {
                "loginid": loginid,
                "is_virtual": 1,
                "currency": account.get("currency"),
                "balance": account.get("balance"),
                "scopes": [],
                "account_list": [{"loginid": loginid, "is_virtual": 1}],
            }
        return await super().authorize()

    async def send(self, msg: dict, timeout: float = 30.0) -> dict:
        root = next((key for key in msg if key not in ("req_id", "passthrough", "subscribe")), None)
        if root not in _EXTRA_KEYS:
            raise RuntimeError(f"Digit Matches blocked message type: {root}")
        if self.ws is None:
            await self.connect()
        assert self.ws is not None
        req_id = self._next_req_id()
        payload = dict(msg)
        payload["req_id"] = req_id
        future = asyncio.get_running_loop().create_future()
        self._pending[req_id] = future
        await self.ws.send(json.dumps(payload))
        try:
            data = await asyncio.wait_for(future, timeout=timeout)
        except Exception:
            self._pending.pop(req_id, None)
            raise
        problem = error_of(data) if isinstance(data, dict) else None
        if problem:
            raise RuntimeError(f"{problem[0]}: {problem[1]}")
        if isinstance(data, dict) and data.get("error"):
            err = data["error"]
            raise RuntimeError(err.get("message") or str(err))
        return data

    async def active_symbols(self) -> list[dict]:
        data = await self.send({"active_symbols": "brief"})
        rows = data.get("active_symbols")
        if not isinstance(rows, list):
            raise RuntimeError("active_symbols missing")
        return rows

    async def contracts_for(self, symbol: str, currency: str) -> dict:
        # The options API rejects currency on this call. Stake currency is sent on proposal.
        del currency
        data = await self.send({"contracts_for": symbol})
        body = data.get("contracts_for")
        if not isinstance(body, dict):
            raise RuntimeError("contracts_for missing")
        return body

    async def ticks_history(self, symbol: str, *, count: int, end: str | int) -> list[dict]:
        try:
            rows = await self.send(
                {
                    "ticks_history": symbol,
                    "adjust_start_time": 1,
                    "count": int(count),
                    "end": end,
                    "style": "ticks",
                },
                timeout=60,
            )
        except RuntimeError as exc:
            text = str(exc)
            if text.startswith("RateLimit"):
                from app.digitmatch.errors import DerivCallError

                raise DerivCallError("RateLimit", text) from exc
            raise
        rows, _pip = parse_history_ticks(rows if isinstance(rows, dict) else {})
        return rows

    async def subscribe_ticks(self, symbol: str, handler) -> None:
        async def _on_message(data: dict) -> None:
            if isinstance(data, dict) and data.get("msg_type") == "tick":
                result = handler(parse_tick(data))
                if asyncio.iscoroutine(result):
                    await result

        self.set_stream_handler(_on_message)
        first = await self.send({"ticks": symbol, "subscribe": 1})
        if isinstance(first, dict) and first.get("msg_type") == "tick":
            result = handler(parse_tick(first))
            if asyncio.iscoroutine(result):
                await result

    async def proposal(self, *, symbol: str, digit: int, stake: float, currency: str) -> dict:
        message = proposal_request(symbol=symbol, digit=digit, stake=stake, currency=currency)
        if self._use_pat:
            message.pop("symbol", None)
            message["underlying_symbol"] = symbol
        data = await self.send(message)
        return parse_proposal(data)

    async def buy(self, proposal_id: str, price: float) -> dict:
        if self.ws is None:
            raise PurchaseNotSent("not connected")
        req_id = self._next_req_id()
        payload = {"buy": proposal_id, "price": float(price), "req_id": req_id}
        future = asyncio.get_running_loop().create_future()
        self._pending[req_id] = future
        try:
            await self.ws.send(json.dumps(payload))
        except Exception as exc:
            self._pending.pop(req_id, None)
            raise PurchaseNotSent("buy message was not written") from exc
        try:
            data = await asyncio.wait_for(future, 30)
        except Exception as exc:
            self._pending.pop(req_id, None)
            raise PurchaseOutcomeUnknown("buy response was not received") from exc
        self._pending.pop(req_id, None)
        if isinstance(data, dict) and data.get("error"):
            err = data["error"]
            raise PurchaseRejected(str(err.get("message") or err))
        try:
            return parse_buy(data)
        except Exception as exc:
            raise PurchaseOutcomeUnknown("buy response could not be parsed") from exc

    async def open_contract(self, contract_id: str) -> dict:
        data = await self.send({"proposal_open_contract": 1, "contract_id": int(contract_id)})
        return parse_open_contract(data)

    async def balance(self) -> dict:
        return await self.get_balance()

    async def statement(self, limit: int = 50) -> list[dict]:
        data = await self.send({"statement": 1, "description": 1, "limit": int(limit), "offset": 0})
        body = data.get("statement") or {}
        transactions = body.get("transactions") if isinstance(body, dict) else None
        if not isinstance(transactions, list):
            raise RuntimeError("statement transactions missing")
        return transactions
