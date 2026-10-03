"""
Signal lifecycle management — handles signal creation, validation, expiry, and outcome tracking.
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Optional

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.signal import Signal, generate_signal_id
from app.models.outcome import SignalOutcome
from app.models.settings import ContractSettings

logger = structlog.get_logger(__name__)


class SignalLifecycleManager:
    """
    Manages the full lifecycle of signals from creation to outcome resolution.
    """

    async def create_signal(
        self,
        session: AsyncSession,
        *,
        symbol: str,
        direction: str,
        duration_seconds: int,
        barrier_input: str,
        barrier_unit: str,
        reference_price: float,
        resolved_barrier: Optional[float],
        quote_id: Optional[int],
        quote_timestamp: Optional[datetime],
        purchase_price: Optional[float],
        total_payout: Optional[float],
        breakeven_probability: Optional[float],
        calibrated_probability: Optional[float],
        raw_probability: Optional[float],
        ev_net: Optional[float],
        ev_margin: Optional[float],
        strategy_name: Optional[str],
        strategy_params: Optional[dict],
        explanation: str,
        evidence_limitations: str,
        model_version_id: Optional[int],
        is_validated: bool,
        day_date: str,
        day_sequence: int,
    ) -> Signal:
        """Create a new signal with full context."""
        signal = Signal(
            signal_id=generate_signal_id(),
            symbol=symbol,
            touch_direction=direction,
            duration_seconds=duration_seconds,
            barrier_input=barrier_input,
            barrier_unit=barrier_unit,
            reference_price=reference_price,
            resolved_barrier=resolved_barrier,
            quote_id=quote_id,
            quote_timestamp=quote_timestamp,
            purchase_price=purchase_price,
            total_payout=total_payout,
            breakeven_probability=breakeven_probability,
            calibrated_probability=calibrated_probability,
            raw_probability=raw_probability,
            ev_net=ev_net,
            ev_margin=ev_margin,
            strategy_name=strategy_name,
            strategy_params=json.dumps(strategy_params) if strategy_params else None,
            explanation=explanation,
            evidence_limitations=evidence_limitations,
            model_version_id=model_version_id,
            is_validated=is_validated,
            day_date=day_date,
            day_sequence=day_sequence,
            status="active",
            valid_until=datetime.now(timezone.utc) + timedelta(seconds=duration_seconds),
        )

        session.add(signal)
        await session.commit()
        await session.refresh(signal)

        logger.info("signal_created",
                     signal_id=signal.signal_id,
                     direction=direction,
                     calibrated_prob=calibrated_probability,
                     is_validated=is_validated)

        return signal

    async def invalidate_signal(
        self,
        session: AsyncSession,
        signal_id: str,
        reason: str,
    ):
        """Invalidate a signal (stale data, connection failure, etc.)."""
        await session.execute(
            update(Signal)
            .where(Signal.signal_id == signal_id)
            .values(
                status="invalidated",
                invalidated_at=datetime.now(timezone.utc),
                invalidation_reason=reason,
            )
        )
        await session.commit()
        logger.info("signal_invalidated", signal_id=signal_id, reason=reason)

    async def record_entry(
        self,
        session: AsyncSession,
        signal_id: str,
        entry_price: float,
        entry_time: Optional[datetime] = None,
        notes: str = "",
    ):
        """Record manual entry for a signal."""
        await session.execute(
            update(Signal)
            .where(Signal.signal_id == signal_id)
            .values(
                status="entered",
                manually_entered=True,
                entry_time=entry_time or datetime.now(timezone.utc),
                entry_price=entry_price,
                entry_notes=notes,
            )
        )
        await session.commit()
        logger.info("signal_entry_recorded", signal_id=signal_id, entry_price=entry_price)

    async def resolve_outcome(
        self,
        session: AsyncSession,
        signal_id: str,
        outcome: dict,
    ):
        """Record the verified outcome for a signal."""
        signal_outcome = SignalOutcome(
            signal_id=signal_id,
            barrier_touched=outcome.get("touched"),
            touch_time=outcome.get("touch_time"),
            touch_epoch=outcome.get("touch_epoch"),
            touch_price=outcome.get("touch_price"),
            window_start=outcome.get("window_start"),
            window_end=outcome.get("window_end"),
            ticks_in_window=outcome.get("ticks_in_window"),
            max_price_in_window=outcome.get("max_price"),
            min_price_in_window=outcome.get("min_price"),
            price_at_start=outcome.get("price_at_start"),
            price_at_end=outcome.get("price_at_end"),
            max_favorable_excursion=outcome.get("max_favorable_excursion"),
            purchase_price=outcome.get("purchase_price"),
            payout=outcome.get("payout"),
            net_pnl=outcome.get("net_pnl"),
            is_hypothetical=outcome.get("is_hypothetical", True),
            evaluation_method="tick_verified",
        )

        session.add(signal_outcome)
        await session.execute(
            update(Signal)
            .where(Signal.signal_id == signal_id)
            .values(status="resolved")
        )
        await session.commit()
        logger.info("signal_resolved",
                     signal_id=signal_id,
                     touched=outcome.get("touched"))

    async def expire_stale_signals(self, session: AsyncSession):
        """Expire signals past their valid_until time."""
        now = datetime.now(timezone.utc)
        result = await session.execute(
            update(Signal)
            .where(Signal.status == "active")
            .where(Signal.valid_until < now)
            .values(status="expired")
        )
        await session.commit()
        if result.rowcount > 0:
            logger.info("signals_expired", count=result.rowcount)

    async def get_active_signals(self, session: AsyncSession) -> list[Signal]:
        """Get currently active signals."""
        result = await session.execute(
            select(Signal)
            .where(Signal.status.in_(["active", "entered"]))
            .order_by(Signal.created_at.desc())
        )
        return result.scalars().all()

    async def get_signals_history(
        self,
        session: AsyncSession,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Signal]:
        """Get signal history."""
        result = await session.execute(
            select(Signal)
            .order_by(Signal.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return result.scalars().all()
