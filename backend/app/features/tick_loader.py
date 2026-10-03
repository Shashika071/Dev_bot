"""Bounded / windowed tick loading for multi-week DB training."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import pandas as pd
import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tick import Tick

logger = structlog.get_logger(__name__)


async def load_ticks_dataframe(
    session: AsyncSession,
    *,
    symbol: str,
    max_ticks: int = 0,
    since_epoch: Optional[int] = None,
    until_epoch: Optional[int] = None,
) -> pd.DataFrame:
    """
    Load ticks ordered by epoch ascending.

    max_ticks > 0 keeps the most recent N ticks (still returned ascending).
    """
    q = select(Tick).where(Tick.symbol == symbol)
    if since_epoch is not None:
        q = q.where(Tick.epoch >= int(since_epoch))
    if until_epoch is not None:
        q = q.where(Tick.epoch <= int(until_epoch))

    if max_ticks and max_ticks > 0:
        # Fetch newest max_ticks, then reverse to ascending
        sub = (
            q.order_by(Tick.epoch.desc())
            .limit(int(max_ticks))
            .subquery()
        )
        # Re-select via ORM using epoch filter from subquery bounds is heavier;
        # simpler: execute desc limit then reverse in Python.
        result = await session.execute(q.order_by(Tick.epoch.desc()).limit(int(max_ticks)))
        ticks = list(reversed(result.scalars().all()))
    else:
        result = await session.execute(q.order_by(Tick.epoch.asc()))
        ticks = result.scalars().all()

    if not ticks:
        return pd.DataFrame(columns=["epoch", "tick_time", "quote"])

    df = pd.DataFrame(
        [
            {
                "epoch": int(t.epoch),
                "tick_time": t.tick_time,
                "quote": float(t.quote),
            }
            for t in ticks
        ]
    )
    logger.info(
        "ticks_loaded",
        symbol=symbol,
        n=len(df),
        max_ticks=max_ticks or None,
        since_epoch=since_epoch,
        until_epoch=until_epoch,
    )
    return df


async def tick_coverage_stats(session: AsyncSession, symbol: Optional[str] = None) -> dict:
    """Aggregate tick coverage / gap-ish stats for health APIs."""
    q = select(
        Tick.symbol,
        func.count(Tick.id).label("tick_count"),
        func.min(Tick.epoch).label("oldest_epoch"),
        func.max(Tick.epoch).label("newest_epoch"),
        func.min(Tick.tick_time).label("oldest"),
        func.max(Tick.tick_time).label("newest"),
        func.sum(Tick.is_gap).label("gap_flags"),
    ).group_by(Tick.symbol)
    if symbol:
        q = q.where(Tick.symbol == symbol)
    rows = (await session.execute(q)).all()
    symbols = []
    for r in rows:
        span = (int(r.newest_epoch) - int(r.oldest_epoch)) if r.oldest_epoch and r.newest_epoch else 0
        days = max(span / 86400.0, 1e-9)
        symbols.append(
            {
                "symbol": r.symbol,
                "tick_count": int(r.tick_count),
                "oldest": str(r.oldest) if r.oldest else None,
                "newest": str(r.newest) if r.newest else None,
                "oldest_epoch": int(r.oldest_epoch) if r.oldest_epoch else None,
                "newest_epoch": int(r.newest_epoch) if r.newest_epoch else None,
                "gap_flags": int(r.gap_flags or 0),
                "est_ticks_per_day": float(r.tick_count) / days,
                "span_days": span / 86400.0,
            }
        )
    return {
        "symbols": symbols,
        "total_ticks": sum(s["tick_count"] for s in symbols),
        "as_of": datetime.utcnow().isoformat() + "Z",
    }
