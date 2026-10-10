"""
Deriv WebSocket client with reconnection, heartbeat, and rate-limit handling.

API: Deriv WebSocket (new public endpoint)
URL: wss://api.derivws.com/trading/v1/options/ws/public
Auth: No app_id or token required for public market data (ticks, proposals, history).
      Use DERIV_API_TOKEN only for account-level authenticated calls.
Permissions: read scope only. NO trade scope requested or used.

IMPORTANT: This client NEVER sends buy, sell, or any order-placement messages.
"""

import asyncio
import json
import time
from typing import Optional, Callable, Any
from datetime import datetime, timezone

import websockets
import websockets.exceptions
import structlog
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type

from app.config import settings
from app.deriv import HEARTBEAT_INTERVAL

logger = structlog.get_logger(__name__)

# Messages that this client is FORBIDDEN from sending
FORBIDDEN_MESSAGES = {"buy", "sell", "sell_expired", "buy_contract_for_multiple_accounts"}


class DerivWSClient:
    """
    Async WebSocket client for Deriv API v3.

    Features:
    - Auto-reconnect with exponential backoff
    - Heartbeat / ping-pong keepalive
    - Rate-limit awareness
    - Message deduplication by req_id
    - Callback-based message routing
    - NEVER sends trading messages (buy/sell)
    """

    def __init__(self):
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self._url = settings.deriv_ws_full_url
        self._handlers: dict[str, list[Callable]] = {}
        self._req_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._connected = False
        self._running = False
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._listen_task: Optional[asyncio.Task] = None
        self._reconnect_count = 0
        self._last_message_time: Optional[float] = None
        self._conn_lock: Optional[asyncio.Lock] = None
        self._listen_ws: Optional[websockets.WebSocketClientProtocol] = None
        # Active subscriptions to replay after reconnect: (request_msg, stream_msg_type)
        self._subscriptions: list[tuple[dict, str]] = []

    def _get_lock(self) -> asyncio.Lock:
        if self._conn_lock is None:
            self._conn_lock = asyncio.Lock()
        return self._conn_lock

    @property
    def connected(self) -> bool:
        return self._connected and self.ws is not None

    @property
    def last_message_age_ms(self) -> Optional[int]:
        if self._last_message_time is None:
            return None
        return int((time.time() - self._last_message_time) * 1000)

    def _next_req_id(self) -> int:
        self._req_id += 1
        return self._req_id

    async def connect(self):
        """Establish WebSocket connection with retry."""
        self._running = True
        async with self._get_lock():
            if self.connected:
                self._ensure_background_tasks()
                return
            await self._connect_with_retry()
            self._ensure_background_tasks()

    def _ensure_background_tasks(self):
        """Start listener and heartbeat tasks if missing or finished."""
        if not self._listen_task or self._listen_task.done():
            self._listen_task = asyncio.create_task(self._listen())
        if not self._heartbeat_task or self._heartbeat_task.done():
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def ensure_connected(self, stale_after_ms: int = 60_000):
        """
        Guarantee a live connection before an API call.
        Reconnects when disconnected or when the socket looks half-open/stale.
        """
        self._running = True
        async with self._get_lock():
            age = self.last_message_age_ms
            stale = age is not None and age > stale_after_ms
            listen_dead = self._listen_task is not None and self._listen_task.done()
            socket_mismatch = (
                self.ws is not None
                and self._listen_ws is not None
                and self.ws is not self._listen_ws
            )

            if self.connected and not stale and not listen_dead and not socket_mismatch:
                self._ensure_background_tasks()
                return

            logger.warning(
                "deriv_ws_ensure_reconnect",
                connected=self.connected,
                stale=stale,
                listen_dead=listen_dead,
                socket_mismatch=socket_mismatch,
                last_message_age_ms=age,
            )
            await self._force_reconnect_unlocked()

    async def force_reconnect(self):
        """Close any existing socket and open a fresh one."""
        self._running = True
        async with self._get_lock():
            await self._force_reconnect_unlocked()

    async def _force_reconnect_unlocked(self):
        """Reconnect assuming the caller already holds `_conn_lock`."""
        self._connected = False
        old_ws = self.ws
        self.ws = None
        self._listen_ws = None
        if old_ws is not None:
            try:
                await old_ws.close()
            except Exception:
                pass

        # Fail pending waiters so callers don't hang on the dead socket
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(ConnectionError("Deriv WebSocket reconnected"))
        self._pending.clear()

        await self._connect_with_retry()
        self._ensure_background_tasks()

    @retry(
        wait=wait_exponential(multiplier=1, min=1, max=60),
        stop=stop_after_attempt(50),
        retry=retry_if_exception_type(Exception),
    )
    async def _connect_with_retry(self):
        try:
            self.ws = await websockets.connect(
                self._url,
                ping_interval=HEARTBEAT_INTERVAL,
                ping_timeout=10,
                close_timeout=5,
                max_size=2**20,
                extra_headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Origin": "https://app.deriv.com"
                }
            )
            self._connected = True
            self._last_message_time = time.time()
            self._reconnect_count += 1
            logger.info("deriv_ws_connected", url=self._url, reconnect_count=self._reconnect_count)
            # Re-subscribe after every successful (re)connect
            await self._resubscribe()

        except Exception as e:
            self._connected = False
            logger.error("deriv_ws_connect_failed", error=str(e))
            raise

    async def disconnect(self):
        """Gracefully close the connection."""
        self._running = False
        self._connected = False

        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        if self._listen_task:
            self._listen_task.cancel()
        if self.ws:
            await self.ws.close()
            self.ws = None

        # Cancel pending requests
        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()
        logger.info("deriv_ws_disconnected")

    async def send(self, msg: dict, timeout: float = 30.0) -> dict:
        """
        Send a message and wait for the response.
        SAFETY: Refuses to send any trading messages.
        """
        # Block forbidden messages
        msg_type = next((k for k in msg if k in FORBIDDEN_MESSAGES), None)
        if msg_type:
            raise RuntimeError(
                f"BLOCKED: Attempted to send forbidden trading message '{msg_type}'. "
                "This bot NEVER places, purchases, or manages trades."
            )

        if not self.connected:
            await self.ensure_connected()
        if not self.connected:
            raise ConnectionError("Not connected to Deriv WebSocket")

        req_id = self._next_req_id()
        msg["req_id"] = req_id

        future = asyncio.get_event_loop().create_future()
        self._pending[req_id] = future

        try:
            await self.ws.send(json.dumps(msg))
            logger.debug("deriv_ws_sent", msg_type=list(msg.keys())[0], req_id=req_id)
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            # Half-open sockets time out instead of raising ConnectionClosed
            self._connected = False
            raise TimeoutError(f"Request {req_id} timed out after {timeout}s")
        except Exception:
            self._pending.pop(req_id, None)
            self._connected = False
            raise

    async def send_and_subscribe(
        self,
        msg: dict,
        handler: Callable,
        stream_msg_type: Optional[str] = None,
    ):
        """
        Send a subscription message and register a handler for updates.

        Deriv often uses a plural request key (e.g. "ticks") but streams
        singular msg_type values (e.g. "tick"). Pass stream_msg_type for that case.
        """
        request_key = list(msg.keys())[0]
        handler_type = stream_msg_type or request_key
        # One active handler per stream type (avoids duplicates after collector restart)
        self._handlers[handler_type] = [handler]

        # Persist a clean copy for reconnect replay (dedupe by request body)
        sub_msg = {k: v for k, v in msg.items() if k not in ("req_id", "subscribe")}
        self._subscriptions = [(m, t) for (m, t) in self._subscriptions if m != sub_msg]
        self._subscriptions.append((sub_msg, handler_type))

        if not self.connected:
            raise ConnectionError("Not connected to Deriv WebSocket")

        await self._send_subscription(sub_msg)

    async def _send_subscription(self, msg: dict):
        """Send one subscribe request on the current socket."""
        payload = dict(msg)
        payload["subscribe"] = 1
        payload["req_id"] = self._next_req_id()
        await self.ws.send(json.dumps(payload))
        logger.info(
            "deriv_ws_subscribed",
            msg_type=list(msg.keys())[0],
            req_id=payload["req_id"],
        )

    async def _resubscribe(self):
        """Replay stored subscriptions after a reconnect."""
        if not self._subscriptions or not self.ws:
            return
        for msg, _handler_type in self._subscriptions:
            try:
                await self._send_subscription(msg)
            except Exception as e:
                logger.warning("deriv_ws_resubscribe_failed", error=str(e), msg=msg)

    def on(self, msg_type: str, handler: Callable):
        """Register a callback for a message type (idempotent per handler)."""
        if msg_type not in self._handlers:
            self._handlers[msg_type] = []
        if handler not in self._handlers[msg_type]:
            self._handlers[msg_type].append(handler)

    async def _listen(self):
        """Main message loop with auto-reconnect."""
        while self._running:
            try:
                if not self.ws or not self._connected:
                    async with self._get_lock():
                        if not self.ws or not self._connected:
                            await self._connect_with_retry()

                ws = self.ws
                if ws is None:
                    await asyncio.sleep(1)
                    continue

                self._listen_ws = ws
                async for raw_msg in ws:
                    self._last_message_time = time.time()
                    try:
                        data = json.loads(raw_msg)
                    except json.JSONDecodeError:
                        logger.warning("deriv_ws_invalid_json", raw=raw_msg[:200])
                        continue

                    await self._route_message(data)

            except websockets.exceptions.ConnectionClosed as e:
                logger.warning("deriv_ws_connection_closed", code=e.code, reason=e.reason)
                # Don't clobber a newer socket that replaced this one mid-listen
                if self.ws is ws or self.ws is None:
                    self._connected = False
                    self._listen_ws = None
                if self._running:
                    await asyncio.sleep(2)
            except Exception as e:
                logger.error("deriv_ws_listen_error", error=str(e))
                if self.ws is None or self._listen_ws is self.ws:
                    self._connected = False
                    self._listen_ws = None
                if self._running:
                    await asyncio.sleep(5)

    async def _route_message(self, data: dict):
        """Route incoming message to handlers and pending futures."""
        # Handle errors
        if "error" in data:
            logger.warning("deriv_ws_error", error=data["error"])
            req_id = data.get("req_id")
            if req_id and req_id in self._pending:
                self._pending.pop(req_id).set_exception(
                    RuntimeError(f"Deriv API error: {data['error']}")
                )
            return

        # Resolve pending request
        req_id = data.get("req_id")
        if req_id and req_id in self._pending:
            self._pending.pop(req_id).set_result(data)

        # Call registered handlers
        msg_type = data.get("msg_type", "")
        if msg_type in self._handlers:
            for handler in self._handlers[msg_type]:
                try:
                    result = handler(data)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as e:
                    logger.error("deriv_ws_handler_error",
                                 msg_type=msg_type, error=str(e))

    async def _heartbeat_loop(self):
        """Send periodic pings to keep the connection alive."""
        while self._running and self._connected:
            try:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                if self.connected:
                    # New API uses standard WebSocket ping frames; fall back to JSON ping
                    try:
                        await self.ws.ping()
                    except Exception:
                        await self.ws.send(json.dumps({"ping": 1}))
            except Exception:
                pass

    # --- Convenience methods for public (non-trading) operations ---

    async def get_active_symbols(self, product_type: str = "basic") -> dict:
        """Fetch available instruments."""
        return await self.send({
            "active_symbols": product_type,
        })

    async def get_contracts_for(self, symbol: str) -> dict:
        """Fetch available contract types for a symbol."""
        return await self.send({
            "contracts_for": symbol,
        })

    async def get_ticks_history(
        self,
        symbol: str,
        end: int | str = "latest",
        start: Optional[int] = None,
        style: str = "ticks",
        count: int = 1000,
        granularity: int | None = None,
    ) -> dict:
        """
        Fetch historical tick or candle data.

        Public Options WS currently returns up to ~1000 ticks ending at `end`.
        Omit `start` and page backward by lowering `end` for longer history.
        For style=candles, pass granularity in seconds (60 / 300 / 900).
        """
        msg = {
            "ticks_history": symbol,
            "end": end,
            "style": style,
            "count": count,
            "adjust_start_time": 1,
        }
        if start is not None:
            msg["start"] = start
        if style == "candles":
            msg["granularity"] = int(granularity or 60)
        return await self.send(msg, timeout=60.0)

    async def get_candles(
        self,
        symbol: str,
        *,
        granularity: int = 60,
        count: int = 80,
    ) -> list[dict]:
        """
        Official Deriv OHLC candles (public WS).
        granularity: 60=1m, 300=5m, 900=15m.
        Returns list of {epoch, open, high, low, close}.
        """
        data = await self.get_ticks_history(
            symbol,
            end="latest",
            style="candles",
            count=int(count),
            granularity=int(granularity),
        )
        raw = data.get("candles") or data.get("history") or []
        if isinstance(raw, dict):
            # Some payloads nest under history-like keys
            raw = raw.get("candles") or []
        out: list[dict] = []
        for c in raw or []:
            if not isinstance(c, dict):
                continue
            try:
                out.append({
                    "epoch": int(c.get("epoch") or c.get("open_time") or 0),
                    "open": float(c["open"]),
                    "high": float(c["high"]),
                    "low": float(c["low"]),
                    "close": float(c["close"]),
                })
            except (KeyError, TypeError, ValueError):
                continue
        out.sort(key=lambda x: x["epoch"])
        return out

    async def subscribe_ticks(self, symbol: str, handler: Callable):
        """Subscribe to live tick stream (request key 'ticks', stream msg_type 'tick')."""
        await self.send_and_subscribe(
            {"ticks": symbol},
            handler,
            stream_msg_type="tick",
        )

    async def get_proposal(
        self,
        symbol: str,
        contract_type: str,
        duration: int,
        duration_unit: str,
        barrier: str,
        amount: float = 10.0,
        basis: str = "payout",
        currency: str = "USD",
    ) -> dict:
        """
        Get a price proposal (quote) for a contract.
        This does NOT purchase the contract — it only requests pricing.
        Note: new API uses 'underlying_symbol' instead of 'symbol'.
        """
        return await self.send({
            "proposal": 1,
            "amount": amount,
            "basis": basis,
            "contract_type": contract_type,
            "currency": currency,
            "duration": duration,
            "duration_unit": duration_unit,
            # Options public WS rejects classic "symbol"; use underlying_symbol only
            "underlying_symbol": symbol,
            "barrier": barrier,
        })

    async def authorize(self, token: str) -> dict:
        """Authorize with read-only token. NOT for trading."""
        return await self.send({"authorize": token})
