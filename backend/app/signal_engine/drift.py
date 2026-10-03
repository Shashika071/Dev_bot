"""Live performance / drift monitor — auto-pause validated alerts when degraded."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.ml.calibration import _wilson_ci
from app.models.outcome import SignalOutcome
from app.models.settings import ContractSettings
from app.models.signal import Signal
from app.signal_engine.settings_lookup import get_latest_confirmed_settings

logger = structlog.get_logger(__name__)


async def compute_live_performance(
    session: AsyncSession,
    *,
    days: int = 7,
    symbol: Optional[str] = None,
    validated_only: bool = True,
) -> dict:
    """Rolling win rate / breakeven from resolved outcomes."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    q = (
        select(Signal, SignalOutcome)
        .join(SignalOutcome, SignalOutcome.signal_id == Signal.signal_id)
        .where(Signal.created_at >= since)
        .where(Signal.status == "resolved")
    )
    if symbol:
        q = q.where(Signal.symbol == symbol)
    if validated_only:
        q = q.where(Signal.is_validated.is_(True))

    rows = (await session.execute(q)).all()
    if not rows:
        return {
            "resolved": 0,
            "wins": 0,
            "win_rate": None,
            "ci_lower": None,
            "ci_upper": None,
            "mean_breakeven": None,
            "mean_calibrated": None,
        }

    wins = 0
    bes = []
    cals = []
    for sig, out in rows:
        if out.barrier_touched:
            wins += 1
        if sig.breakeven_probability is not None:
            bes.append(float(sig.breakeven_probability))
        if sig.calibrated_probability is not None:
            cals.append(float(sig.calibrated_probability))

    n = len(rows)
    ci_lo, ci_hi = _wilson_ci(wins, n)
    return {
        "resolved": n,
        "wins": wins,
        "win_rate": wins / n,
        "ci_lower": ci_lo,
        "ci_upper": ci_hi,
        "mean_breakeven": sum(bes) / len(bes) if bes else None,
        "mean_calibrated": sum(cals) / len(cals) if cals else None,
        "days": days,
    }


async def maybe_auto_pause_alerts(session: AsyncSession) -> Optional[dict]:
    """
    If enough resolved validated signals exist and Wilson CI lower falls below
    mean quote breakeven (+ margin), pause alerts on confirmed settings.
    """
    if not settings.auto_pause_enabled:
        return None

    conf = await get_latest_confirmed_settings(session)
    if not conf:
        return None

    perf = await compute_live_performance(
        session, days=7, symbol=conf.symbol, validated_only=True
    )
    n = int(perf.get("resolved") or 0)
    if n < settings.auto_pause_min_resolved:
        return {"action": "none", "reason": "insufficient_resolved", "performance": perf}

    mean_be = perf.get("mean_breakeven")
    ci_lo = perf.get("ci_lower")
    if mean_be is None or ci_lo is None:
        return {"action": "none", "reason": "missing_metrics", "performance": perf}

    threshold = float(mean_be) + float(settings.auto_pause_ci_margin)
    if float(ci_lo) >= threshold:
        # Recover from pause if previously paused for drift
        if getattr(conf, "alerts_paused", False) and (
            conf.pause_reason or ""
        ).startswith("auto_pause:"):
            await session.execute(
                update(ContractSettings)
                .where(ContractSettings.id == conf.id)
                .values(alerts_paused=False, pause_reason=None, paused_at=None)
            )
            await session.commit()
            logger.info("alerts_auto_resumed", performance=perf)
            return {"action": "resume", "performance": perf}
        return {"action": "none", "reason": "healthy", "performance": perf}

    reason = (
        f"auto_pause: live CI lower {ci_lo:.4f} < breakeven+margin {threshold:.4f} "
        f"(n={n}, win_rate={perf.get('win_rate')})"
    )
    await session.execute(
        update(ContractSettings)
        .where(ContractSettings.id == conf.id)
        .values(
            alerts_paused=True,
            pause_reason=reason,
            paused_at=datetime.now(timezone.utc),
        )
    )
    await session.commit()
    logger.warning("alerts_auto_paused", reason=reason, performance=perf)
    return {"action": "pause", "reason": reason, "performance": perf}
