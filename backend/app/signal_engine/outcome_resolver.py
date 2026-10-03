"""Resolve matured signals against stored ticks (touch verification)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.deriv.barriers import barrier_magnitude
from app.features.labels import label_touch_outcomes
from app.models.outcome import SignalOutcome
from app.models.signal import Signal
from app.models.tick import Tick
from app.signal_engine.lifecycle import SignalLifecycleManager

logger = structlog.get_logger(__name__)


async def resolve_pending_outcomes(
    session: AsyncSession,
    *,
    symbol: Optional[str] = None,
    limit: int = 50,
) -> int:
    """
    Find expired/entered/active signals past their window and write SignalOutcome rows.
    Returns number of newly resolved signals.
    """
    now = datetime.now(timezone.utc)
    q = (
        select(Signal)
        .where(Signal.status.in_(["active", "entered", "expired"]))
        .order_by(Signal.created_at.asc())
        .limit(limit)
    )
    if symbol:
        q = q.where(Signal.symbol == symbol)

    signals = (await session.execute(q)).scalars().all()
    if not signals:
        return 0

    # Skip already resolved
    existing = (
        await session.execute(
            select(SignalOutcome.signal_id).where(
                SignalOutcome.signal_id.in_([s.signal_id for s in signals])
            )
        )
    ).scalars().all()
    already = set(existing)

    lifecycle = SignalLifecycleManager()
    resolved = 0

    for sig in signals:
        if sig.signal_id in already:
            continue
        created = sig.created_at
        if created is None:
            continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        window_end = created.timestamp() + int(sig.duration_seconds or 540)
        if now.timestamp() < window_end + 2:
            continue  # still in window

        # Load ticks covering entry → window end (+small pad)
        entry_epoch = int(created.timestamp())
        pad_start = entry_epoch - 30
        pad_end = int(window_end) + 30
        ticks = (
            await session.execute(
                select(Tick)
                .where(Tick.symbol == sig.symbol)
                .where(Tick.epoch >= pad_start)
                .where(Tick.epoch <= pad_end)
                .order_by(Tick.epoch.asc())
            )
        ).scalars().all()
        if len(ticks) < settings.min_label_window_ticks:
            logger.debug("outcome_insufficient_ticks", signal_id=sig.signal_id, n=len(ticks))
            continue

        import pandas as pd

        ticks_df = pd.DataFrame(
            [{"epoch": t.epoch, "tick_time": t.tick_time, "quote": float(t.quote)} for t in ticks]
        )
        direction = str(sig.touch_direction or "upper").lower()
        try:
            dist = float(barrier_magnitude(sig.barrier_input))
        except Exception:
            dist = abs(float(str(sig.barrier_input).replace("+", "").replace("-", "") or 0.1))

        labels = label_touch_outcomes(
            ticks_df=ticks_df,
            entry_times=pd.Series([created]),
            duration_seconds=int(sig.duration_seconds or 540),
            barrier_distance=dist,
            barrier_direction=direction,
            entry_delay_seconds=settings.manual_entry_delay_seconds,
            min_window_ticks=settings.min_label_window_ticks,
        )
        if labels.empty:
            continue
        row = labels.iloc[0]
        if not bool(row.get("resolved", False)):
            continue

        touched = bool(row["touched"])
        purchase = float(sig.purchase_price or 0)
        payout = float(sig.total_payout or 0)
        net = (payout - purchase) if touched else (-purchase if purchase else None)

        outcome = {
            "touched": touched,
            "touch_time": row.get("touch_time") if touched else None,
            "touch_epoch": int(row["touch_time"].timestamp()) if touched and hasattr(row.get("touch_time"), "timestamp") else None,
            "touch_price": float(row["touch_price"]) if touched and row.get("touch_price") == row.get("touch_price") else None,
            "window_start": created,
            "window_end": datetime.fromtimestamp(window_end, tz=timezone.utc),
            "ticks_in_window": int(row.get("ticks_in_window") or 0),
            "max_price": float(row["max_price"]) if row.get("max_price") == row.get("max_price") else None,
            "min_price": float(row["min_price"]) if row.get("min_price") == row.get("min_price") else None,
            "price_at_start": float(row.get("entry_price") or 0) or None,
            "price_at_end": float(row["price_at_end"]) if row.get("price_at_end") == row.get("price_at_end") else None,
            "max_favorable_excursion": float(row["max_favorable_excursion"]) if row.get("max_favorable_excursion") == row.get("max_favorable_excursion") else None,
            "purchase_price": purchase or None,
            "payout": payout or None,
            "net_pnl": net,
            "is_hypothetical": not bool(sig.manually_entered),
        }
        await lifecycle.resolve_outcome(session, sig.signal_id, outcome)
        resolved += 1

    if resolved:
        logger.info("outcomes_resolved", count=resolved)
    return resolved
