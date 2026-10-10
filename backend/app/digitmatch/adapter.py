"""Classic Deriv WebSocket v3 adapter.

URL: wss://ws.derivws.com/websockets/v3?app_id=<app id>
Authentication: one authorize message with a classic API token.
PAT, OAuth, OTP, and the options trading/v1 schema are not used.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Awaitable, Callable

import structlog
import websockets

from app.config import settings
from app.digitmatch.account import assert_classic_token
from app.digitmatch.errors import DerivCallError, PurchaseNotSent, PurchaseOutcomeUnknown, PurchaseRejected
from app.digitmatch.messages import (
    authorize_request,
    buy_request,
    error_of,
    history_request,
    parse_buy,
    parse_history_ticks,
    parse_open_contract,
    parse_proposal,
    parse_tick,
    proposal_request,
)
from app.digitmatch.redaction import safe_message_summary

logger = structlog.get_logger(__name__)


class DerivV3Adapter:
    def __init__(self, token: str, app_id: str, url: str | None = None):
        self.token = assert_classic_token(token)
        self.app_id = str(app_id).strip()
        if not self.app_id:
            raise RuntimeError("DM_DERIV_APP_ID is required for the classic WebSocket API")
        base = (url or settings.dm_deriv_ws_url).rstrip("/")
        self.url = base if "app_id=" in base else f"{base}?app_id={self.app_id}"
        self.ws = None
        self._req_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._running = False
        self._listen_task: asyncio.Task | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._subscriptions: list[str] = []
        self._tick_handler: Callable[[dict], Awaitable[None] | None] | None = None
        self._last_message = 0.0

    @property
    def connected(self) -> bool:
        return self.ws is not None and self._running

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    async def connect(self) -> None:
        self._running = True
        self.ws = await websockets.connect(
            self.url,
            ping_interval=30,
            ping_timeout=10,
            close_timeout=5,
            max_size=2**22,
        )
        self._last_message = time.time()
        self._listen_task = asyncio.create_task(self._listen())
        self._heartbeat_task = asyncio.create_task(self._heartbeat())
        logger.info("dm_ws_connected", api_family="deriv_websocket_v3_api_token")

    async def close(self) -> None:
        self._running = False
        for subscription in list(self._subscriptions):
            try:
                await self.request({"forget": subscription}, timeout=5)
            except Exception:
                pass
        self._subscriptions.clear()
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        if self._listen_task:
            self._listen_task.cancel()
        if self.ws is not None:
            await self.ws.close()
            self.ws = None

    async def _heartbeat(self) -> None:
        while self._running:
            await asyncio.sleep(30)
            try:
                await self.request({"ping": 1}, timeout=10)
            except Exception as exc:
                logger.warning("dm_heartbeat_failed", error=type(exc).__name__)
                self._running = False
                return

    async def _listen(self) -> None:
        assert self.ws is not None
        try:
            async for raw in self.ws:
                self._last_message = time.time()
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(data, dict):
                    continue
                req_id = data.get("req_id")
                future = self._pending.get(req_id) if req_id is not None else None
                subscription = data.get("subscription") or {}
                if isinstance(subscription, dict) and subscription.get("id"):
                    sub_id = str(subscription["id"])
                    if sub_id not in self._subscriptions:
                        self._subscriptions.append(sub_id)
                if future is not None and not future.done():
                    future.set_result(data)
                if data.get("msg_type") == "tick" and self._tick_handler is not None:
                    try:
                        maybe = self._tick_handler(parse_tick(data))
                        if asyncio.iscoroutine(maybe):
                            await maybe
                    except Exception as exc:
                        logger.warning("dm_tick_handler_failed", error=type(exc).__name__)
        except Exception as exc:
            logger.warning("dm_listen_ended", error=type(exc).__name__)
            self._running = False
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(ConnectionError("digitmatch socket closed"))

    async def request(self, message: dict, timeout: float = 30) -> dict:
        if self.ws is None:
            raise ConnectionError("not connected")
        req_id = self._next_id()
        payload = dict(message)
        payload["req_id"] = req_id
        logger.info("dm_ws_send", **safe_message_summary(message))
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._pending[req_id] = future
        try:
            await self.ws.send(json.dumps(payload))
        except Exception as exc:
            self._pending.pop(req_id, None)
            raise ConnectionError("send failed") from exc
        try:
            data = await asyncio.wait_for(future, timeout)
        finally:
            self._pending.pop(req_id, None)
        problem = error_of(data)
        if problem:
            code, text = problem
            if code == "RateLimit":
                raise DerivCallError(code, text)
            raise DerivCallError(code, text)
        return data

    async def authorize(self) -> dict:
        data = await self.request(authorize_request(self.token))
        auth = data.get("authorize")
        if not isinstance(auth, dict):
            raise DerivCallError("bad_authorize", "authorize object missing")
        logger.info(
            "dm_authorized",
            loginid=auth.get("loginid"),
            is_virtual=auth.get("is_virtual"),
            currency=auth.get("currency"),
        )
        return auth

    async def active_symbols(self) -> list[dict]:
        data = await self.request({"active_symbols": "brief", "product_type": "basic"})
        rows = data.get("active_symbols")
        if not isinstance(rows, list):
            raise DerivCallError("bad_symbols", "active_symbols missing")
        return rows

    async def contracts_for(self, symbol: str, currency: str) -> dict:
        data = await self.request(
            {"contracts_for": symbol, "currency": currency, "product_type": "basic"}
        )
        body = data.get("contracts_for")
        if not isinstance(body, dict):
            raise DerivCallError("bad_contracts", "contracts_for missing")
        return body

    async def ticks_history(self, symbol: str, *, count: int, end: str | int) -> list[dict]:
        data = await self.request(history_request(symbol, count=count, end=end), timeout=60)
        rows, _pip = parse_history_ticks(data)
        return rows

    async def subscribe_ticks(self, symbol: str, handler) -> None:
        self._tick_handler = handler
        await self.request({"ticks": symbol, "subscribe": 1})

    async def proposal(self, *, symbol: str, digit: int, stake: float, currency: str) -> dict:
        data = await self.request(proposal_request(symbol=symbol, digit=digit, stake=stake, currency=currency))
        return parse_proposal(data)

    async def buy(self, proposal_id: str, price: float) -> dict:
        message = buy_request(proposal_id, price)
        if self.ws is None:
            raise PurchaseNotSent("not connected")
        req_id = self._next_id()
        payload = dict(message)
        payload["req_id"] = req_id
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
            raise PurchaseOutcomeUnknown("buy response was not received") from exc
        finally:
            self._pending.pop(req_id, None)
        problem = error_of(data)
        if problem:
            raise PurchaseRejected(problem[1])
        try:
            return parse_buy(data)
        except Exception as exc:
            raise PurchaseOutcomeUnknown("buy response could not be parsed") from exc

    async def open_contract(self, contract_id: str) -> dict:
        data = await self.request({"proposal_open_contract": 1, "contract_id": int(contract_id)})
        return parse_open_contract(data)

    async def balance(self) -> dict:
        data = await self.request({"balance": 1})
        body = data.get("balance")
        if not isinstance(body, dict):
            raise DerivCallError("bad_balance", "balance missing")
        return body

    async def statement(self, limit: int = 50) -> list[dict]:
        data = await self.request(
            {"statement": 1, "description": 1, "limit": int(limit), "offset": 0}
        )
        body = data.get("statement") or {}
        transactions = body.get("transactions") if isinstance(body, dict) else None
        if not isinstance(transactions, list):
            raise DerivCallError("bad_statement", "statement transactions missing")
        return transactions

    async def profit_table(self, limit: int = 50) -> list[dict]:
        data = await self.request(
            {"profit_table": 1, "description": 1, "limit": int(limit), "offset": 0, "sort": "DESC"}
        )
        body = data.get("profit_table") or {}
        transactions = body.get("transactions") if isinstance(body, dict) else None
        if not isinstance(transactions, list):
            raise DerivCallError("bad_profit_table", "profit_table transactions missing")
        return transactions

    def stale_stream(self, max_age: float) -> bool:
        if self._last_message <= 0:
            return True
        return (time.time() - self._last_message) > max_age
