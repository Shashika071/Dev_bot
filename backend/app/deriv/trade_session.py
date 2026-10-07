"""
Long-lived trade WebSocket session — event-driven balance updates.

Keeps one authenticated Deriv trade socket open, subscribes to balance
(and transactions when available), and writes the account cache so the UI
can read without opening a new PAT/OTP on every Refresh.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)

# Debounce heavy profit_table pulls after stream events
_PROFIT_REFRESH_MIN_SECONDS = 90.0
# Soft reconnect backoff
_RECONNECT_DELAYS = (3.0, 8.0, 20.0, 45.0)


class TradeAccountSession:
    """Process-wide singleton for live trade-account state."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._task: Optional[asyncio.Task] = None
        self._client: Any = None
        self._want_running = False
        self._connected = False
        self._last_error: Optional[str] = None
        self._last_profit_fetch = 0.0
        self._snapshot: dict[str, Any] = {}
        self._generation = 0

    @property
    def connected(self) -> bool:
        return self._connected

    def status(self) -> dict[str, Any]:
        return {
            "live": self._connected,
            "want_running": self._want_running,
            "last_error": self._last_error,
            "loginid": self._snapshot.get("loginid"),
            "balance": self._snapshot.get("balance"),
            "currency": self._snapshot.get("currency"),
        }

    def snapshot(self) -> dict[str, Any]:
        return dict(self._snapshot) if self._snapshot else {}

    async def start(self) -> None:
        """Start or restart the live session if a trade token exists."""
        async with self._lock:
            self._want_running = True
            self._generation += 1
            gen = self._generation
            if self._task and not self._task.done():
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
            self._task = asyncio.create_task(self._run_loop(gen))

    async def stop(self) -> None:
        async with self._lock:
            self._want_running = False
            self._generation += 1
            if self._task and not self._task.done():
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
            self._task = None
            await self._close_client()
            self._connected = False

    async def restart(self) -> None:
        await self.stop()
        await self.start()

    async def refresh_stats(self) -> dict[str, Any]:
        """Pull profit_table on the live socket (no new OTP)."""
        await self._refresh_profit_table(force=True)
        return self.snapshot()

    async def place_one_touch(self, **kwargs: Any) -> dict[str, Any]:
        """Place a trade on the shared socket when connected; else one-shot client."""
        client = self._client
        if client is not None and self._connected:
            try:
                result = await client.place_one_touch(**kwargs)
                # Balance stream should follow; nudge profit stats soon
                asyncio.create_task(self._refresh_profit_table(force=False))
                return result
            except Exception as e:
                logger.warning("trade_session_place_failed_fallback", error=str(e))

        from app.deriv.trade_client import DerivTradeClient
        from app.trade_prefs import get_trade_token

        token = get_trade_token()
        if not token:
            return {"ok": False, "error": "Deriv API token not configured"}
        one = DerivTradeClient(token)
        try:
            return await one.place_one_touch(**kwargs)
        finally:
            try:
                await one.close()
            except Exception:
                pass

    async def _close_client(self) -> None:
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:
                pass
            self._client = None

    async def _run_loop(self, gen: int) -> None:
        from app.trade_prefs import get_trade_token, token_status

        attempt = 0
        while self._want_running and gen == self._generation:
            if not token_status().get("token_configured") or not get_trade_token():
                self._connected = False
                self._last_error = "No trade token configured"
                await asyncio.sleep(5.0)
                continue
            try:
                await self._connect_and_listen(gen)
                attempt = 0
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._connected = False
                self._last_error = str(e)
                logger.warning("trade_session_loop_error", error=str(e), attempt=attempt)
                await self._close_client()
                delay = _RECONNECT_DELAYS[min(attempt, len(_RECONNECT_DELAYS) - 1)]
                attempt += 1
                await asyncio.sleep(delay)

    async def _connect_and_listen(self, gen: int) -> None:
        from app.deriv.trade_client import DerivTradeClient
        from app.trade_prefs import get_trade_token, resolve_deriv_app_id

        token = get_trade_token()
        if not token:
            raise RuntimeError("No trade token")

        await self._close_client()
        client = DerivTradeClient(token, app_id=resolve_deriv_app_id() or None)
        client.set_stream_handler(self._on_stream)
        self._client = client

        await client.connect()
        if not client._use_pat:
            await client.authorize()

        # Seed on this socket only — never open a second PAT/OTP
        summary = await client.seed_account_snapshot()
        self._apply_summary(summary, source="connect")
        self._connected = True
        self._last_error = None
        logger.info(
            "trade_session_connected",
            loginid=summary.get("loginid"),
            account_type=summary.get("account_type"),
        )

        # Live balance pushes
        try:
            await client.subscribe_balance()
        except Exception as e:
            logger.warning("trade_session_balance_subscribe_failed", error=str(e))

        try:
            await client.subscribe_transactions()
        except Exception as e:
            logger.info("trade_session_transaction_subscribe_skip", error=str(e))

        self._last_profit_fetch = time.time()

        # Keep connection alive; reader runs inside client
        while (
            self._want_running
            and gen == self._generation
            and client.ws is not None
        ):
            try:
                await client.send({"ping": 1}, timeout=15.0)
            except Exception:
                raise ConnectionError("Trade session ping failed")
            await asyncio.sleep(25.0)

        self._connected = False

    async def _on_stream(self, data: dict) -> None:
        msg_type = str(data.get("msg_type") or "")
        if msg_type == "balance" or "balance" in data:
            bal = data.get("balance") if isinstance(data.get("balance"), dict) else data
            if not isinstance(bal, dict):
                return
            try:
                amount = bal.get("balance")
                if amount is not None:
                    self._snapshot["balance"] = float(amount)
                if bal.get("currency"):
                    self._snapshot["currency"] = str(bal.get("currency"))
                loginid = bal.get("loginid") or bal.get("account_id")
                if loginid:
                    self._snapshot["loginid"] = str(loginid)
                self._snapshot["live"] = True
                self._snapshot["updated_at"] = time.time()
                self._snapshot["update_source"] = "balance_stream"
                self._persist_snapshot()
                logger.debug(
                    "trade_session_balance_push",
                    balance=self._snapshot.get("balance"),
                    currency=self._snapshot.get("currency"),
                )
            except Exception as e:
                logger.warning("trade_session_balance_parse_failed", error=str(e))
            return

        if msg_type == "transaction" or "transaction" in data:
            # Contract buy/sell — refresh P/L stats (debounced)
            asyncio.create_task(self._refresh_profit_table(force=False))

    def _apply_summary(self, summary: dict[str, Any], *, source: str) -> None:
        if not summary:
            return
        self._snapshot = {
            **summary,
            "live": True,
            "cached": False,
            "update_source": source,
            "updated_at": time.time(),
            "fetched_at": time.time(),
        }
        self._persist_snapshot()

    def _persist_snapshot(self) -> None:
        try:
            from app.deriv.account_cache import write_account_cache

            write_account_cache(dict(self._snapshot))
        except Exception as e:
            logger.warning("trade_session_cache_write_failed", error=str(e))

    async def _refresh_profit_table(self, *, force: bool = False) -> None:
        now = time.time()
        if not force and (now - self._last_profit_fetch) < _PROFIT_REFRESH_MIN_SECONDS:
            return
        client = self._client
        if client is None or not self._connected:
            return
        self._last_profit_fetch = now
        try:
            pt = await client.get_profit_table(limit=100)
            stats = client._profit_stats_from_table(pt)
            self._snapshot.update(stats)
            self._snapshot["updated_at"] = time.time()
            self._snapshot["update_source"] = "profit_table"
            self._persist_snapshot()
        except Exception as e:
            logger.warning("trade_session_profit_refresh_failed", error=str(e))


trade_account_session = TradeAccountSession()
