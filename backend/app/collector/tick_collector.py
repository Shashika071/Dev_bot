"""
Live tick collector — subscribes to Deriv tick stream and persists to DB.

Features:
- Deduplication by (symbol, epoch)
- Gap detection between ticks
- Latency monitoring (server time vs local receive time)
- Persistent across restarts (checks last stored tick on startup)
"""

import asyncio
import time
from datetime import datetime, timezone
from typing import Optional

import structlog
from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.database import async_session
from app.models.tick import Tick
from app.deriv.client import DerivWSClient

logger = structlog.get_logger(__name__)

# Maximum expected gap between ticks before flagging (seconds)
MAX_TICK_GAP_SECONDS = 5


class TickCollector:
    """
    Subscribes to Deriv live tick stream and persists ticks to PostgreSQL.
    Handles deduplication, gap detection, and latency tracking.
    """

    def __init__(self, client: DerivWSClient, symbol: str):
        self.client = client
        self.symbol = symbol
        self._last_epoch: Optional[int] = None
        self._tick_count = 0
        self._gap_count = 0
        self._running = False

    async def start(self):
        """Start tick collection."""
        self._running = True

        # Recover last stored epoch for continuity across restarts
        async with async_session() as session:
            result = await session.execute(
                select(func.max(Tick.epoch)).where(Tick.symbol == self.symbol)
            )
            self._last_epoch = result.scalar()
            if self._last_epoch:
                logger.info("tick_collector_resuming",
                             symbol=self.symbol, last_epoch=self._last_epoch)

        # Subscribe to live ticks
        await self.client.subscribe_ticks(self.symbol, self._handle_tick)
        logger.info("tick_collector_started", symbol=self.symbol)

    async def stop(self):
        """Stop tick collection."""
        self._running = False
        logger.info("tick_collector_stopped",
                     symbol=self.symbol,
                     total_ticks=self._tick_count,
                     gaps_detected=self._gap_count)

    async def _handle_tick(self, data: dict):
        """Process incoming tick message."""
        if not self._running:
            return

        tick_data = data.get("tick", {})
        if not tick_data:
            return

        epoch = tick_data.get("epoch")
        quote = tick_data.get("quote")
        ask = tick_data.get("ask")
        bid = tick_data.get("bid")
        pip_size = tick_data.get("pip_size")

        if epoch is None or quote is None:
            logger.warning("tick_collector_missing_data", data=tick_data)
            return

        # Deduplication
        if self._last_epoch and epoch <= self._last_epoch:
            return  # Already stored

        receive_time = datetime.now(timezone.utc)
        tick_time = datetime.fromtimestamp(epoch, tz=timezone.utc)
        latency_ms = int((receive_time - tick_time).total_seconds() * 1000)

        # Gap detection
        is_gap = 0
        if self._last_epoch and (epoch - self._last_epoch) > MAX_TICK_GAP_SECONDS:
            is_gap = 1
            self._gap_count += 1
            logger.warning("tick_collector_gap_detected",
                           symbol=self.symbol,
                           gap_seconds=epoch - self._last_epoch,
                           last_epoch=self._last_epoch,
                           current_epoch=epoch)

        # Persist
        try:
            async with async_session() as session:
                stmt = pg_insert(Tick).values(
                    symbol=self.symbol,
                    epoch=epoch,
                    tick_time=tick_time,
                    quote=quote,
                    ask=ask,
                    bid=bid,
                    received_at=receive_time,
                    latency_ms=latency_ms,
                    pip_size=pip_size,
                    is_gap=is_gap,
                ).on_conflict_do_nothing(
                    index_elements=["symbol", "epoch"]
                )
                await session.execute(stmt)
                await session.commit()

            self._last_epoch = epoch
            self._tick_count += 1

            if self._tick_count % 100 == 0:
                logger.info("tick_collector_progress",
                             symbol=self.symbol,
                             count=self._tick_count,
                             latency_ms=latency_ms)

        except Exception as e:
            logger.error("tick_collector_persist_error", error=str(e))

    @property
    def stats(self) -> dict:
        return {
            "symbol": self.symbol,
            "running": self._running,
            "ticks_collected": self._tick_count,
            "gaps_detected": self._gap_count,
            "last_epoch": self._last_epoch,
            "last_message_age_ms": self.client.last_message_age_ms,
        }
