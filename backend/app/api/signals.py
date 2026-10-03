"""
Signals API for listing alerts and recording manual entries.
Optional auto-trade runs only when explicitly enabled in Setup.
"""

import time
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.deriv.auto_trade import maybe_auto_trade_after_signal
from app.models.signal import Signal
from app.models.tick import Tick
from app.notifications.browser import (
    get_latest_status,
    send_signal_notification,
    send_status_update,
)
from app.signal_engine.chart_candles import INTERVAL_SECONDS, aggregate_ohlc
from app.signal_engine.daily_cap import DailyCapManager
from app.signal_engine.lifecycle import SignalLifecycleManager
from app.signal_engine.analyze_watch import analyze_watch
from app.signal_engine.manual_generate import generate_manual_signal
from app.signal_engine.settings_lookup import get_latest_confirmed_settings

router = APIRouter(prefix="/signals", tags=["signals"])
lifecycle = SignalLifecycleManager()


def require_internal_secret(
    x_internal_secret: str | None = Header(default=None, alias="X-Internal-Secret"),
) -> None:
    expected = settings.internal_api_secret
    if not expected or x_internal_secret != expected:
        raise HTTPException(status_code=403, detail="Forbidden")


def _signal_to_dict(signal: Signal) -> dict:
    return {
        "signal_id": signal.signal_id,
        "symbol": signal.symbol,
        "direction": signal.touch_direction,
        "touch_direction": signal.touch_direction,
        "probability": signal.calibrated_probability,
        "calibrated_probability": signal.calibrated_probability,
        "raw_probability": signal.raw_probability,
        "created_at": signal.created_at.isoformat() if signal.created_at else None,
        "status": signal.status,
        "barrier_input": signal.barrier_input,
        "is_validated": signal.is_validated,
        "explanation": signal.explanation,
        "ev_net": signal.ev_net,
        "strategy_name": signal.strategy_name,
        "purchase_price": signal.purchase_price,
        "total_payout": signal.total_payout,
    }


@router.get("/")
async def list_signals(
    limit: int = 50, offset: int = 0, db: AsyncSession = Depends(get_db)
) -> list[Any]:
    """Get history of generated signals."""
    rows = await lifecycle.get_signals_history(db, limit, offset)
    return [_signal_to_dict(s) for s in rows]


@router.get("/active")
async def active_signals(db: AsyncSession = Depends(get_db)) -> list[Any]:
    """Get currently active/unexpired signals."""
    rows = await lifecycle.get_active_signals(db)
    return [_signal_to_dict(s) for s in rows]


@router.get("/chart-ticks")
async def chart_ticks(
    symbol: Optional[str] = None,
    limit: int = Query(default=300, ge=50, le=2000),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Recent ticks for the live Volatility chart on the dashboard."""
    sym = symbol
    if not sym:
        conf = await get_latest_confirmed_settings(db)
        sym = conf.symbol if conf else "R_100"

    result = await db.execute(
        select(Tick)
        .where(Tick.symbol == sym)
        .order_by(Tick.epoch.desc())
        .limit(limit)
    )
    ticks = list(reversed(result.scalars().all()))
    points = [
        {
            "epoch": int(t.epoch),
            "quote": float(t.quote),
            "time": t.tick_time.isoformat() if t.tick_time else None,
        }
        for t in ticks
    ]
    last = points[-1]["quote"] if points else None
    return {
        "symbol": sym,
        "count": len(points),
        "last_quote": last,
        "points": points,
    }


@router.get("/chart-candles")
async def chart_candles(
    symbol: Optional[str] = None,
    interval: str = Query(default="1m", pattern="^(1m|5m|15m)$"),
    limit: int = Query(default=120, ge=20, le=500),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """OHLC candles aggregated from stored ticks (1m / 5m / 15m)."""
    sym = symbol
    if not sym:
        conf = await get_latest_confirmed_settings(db)
        sym = conf.symbol if conf else "R_100"

    step = INTERVAL_SECONDS[interval]
    # Extra buffer so the latest incomplete candle has enough ticks
    lookback = step * (limit + 2)
    min_epoch = int(time.time()) - lookback

    result = await db.execute(
        select(Tick.epoch, Tick.quote)
        .where(Tick.symbol == sym, Tick.epoch >= min_epoch)
        .order_by(Tick.epoch.asc())
    )
    rows = result.all()
    # Fallback: if time window is empty/sparse, take recent ticks by count
    if len(rows) < 50:
        result = await db.execute(
            select(Tick.epoch, Tick.quote)
            .where(Tick.symbol == sym)
            .order_by(Tick.epoch.desc())
            .limit(min(20000, max(2000, limit * step * 3)))
        )
        rows = list(reversed(result.all()))

    candles = aggregate_ohlc(((int(e), float(q)) for e, q in rows), interval, max_candles=limit)
    last = candles[-1]["close"] if candles else None
    return {
        "symbol": sym,
        "interval": interval,
        "count": len(candles),
        "last_quote": last,
        "candles": candles,
    }


@router.post("/generate")
@router.post("/analyze-generate")
async def generate_signal(
    payload: dict | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Analyze live ticks/quotes with trained models, then emit a signal only if
    gates pass. Modes:
      - standard (default): confluence + candles + p + margin
      - force_model_candles: candles + min calibrated p only
    Daily cap / cooldown still apply. Optional auto-trade if enabled in Setup.
    """
    body = payload or {}
    force = bool(body.get("force_no_edge", False))
    mode = str(body.get("mode") or "standard").strip().lower()
    if mode not in ("standard", "force_model_candles"):
        mode = "standard"
    min_p = body.get("min_probability")
    min_probability = float(min_p) if min_p is not None else None
    result = await generate_manual_signal(
        db,
        force_no_edge=force,
        mode=mode,
        min_probability=min_probability,
    )
    if result.get("ok") and result.get("signal"):
        try:
            await send_signal_notification(result["signal"])
        except Exception:
            pass
        try:
            trade = await maybe_auto_trade_after_signal(db, result["signal"])
            result["trade"] = trade
        except Exception as e:
            result["trade"] = {"ok": False, "error": str(e)}
    return result


@router.get("/watch/status")
async def get_analyze_watch_status() -> dict:
    """Current server-side Analyze/Force watcher status (survives tab close)."""
    return analyze_watch.status()


@router.post("/watch/start")
async def start_analyze_watch(payload: dict | None = None) -> dict:
    """Start continuous server-side watch until signal (or Stop / fatal)."""
    body = payload or {}
    mode = str(body.get("mode") or "standard").strip().lower()
    min_p = body.get("min_probability")
    min_probability = float(min_p) if min_p is not None else None
    return await analyze_watch.start(mode=mode, min_probability=min_probability)


@router.post("/watch/stop")
async def stop_analyze_watch() -> dict:
    return await analyze_watch.stop(reason="stopped_by_user")


@router.post("/{signal_id}/entry")
async def record_manual_entry(
    signal_id: str,
    entry_data: dict,
    db: AsyncSession = Depends(get_db),
) -> Any:
    """Record that a user manually placed a trade for a signal."""
    result = await db.execute(select(Signal).where(Signal.signal_id == signal_id))
    signal = result.scalar_one_or_none()
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")

    await lifecycle.record_entry(
        db,
        signal_id=signal_id,
        entry_price=entry_data.get("entry_price", 0.0),
        notes=entry_data.get("notes", ""),
    )
    return {"status": "ok", "message": "Entry recorded"}


@router.get("/status")
async def daily_cap_status(db: AsyncSession = Depends(get_db)) -> dict:
    """Get current daily cap and cooldown status."""
    cap_mgr = DailyCapManager()
    return await cap_mgr.get_status(db)


@router.get("/performance")
async def live_performance(
    days: int = Query(default=7, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Rolling resolved-outcome performance + pause state."""
    from app.signal_engine.drift import compute_live_performance

    conf = await get_latest_confirmed_settings(db)
    perf = await compute_live_performance(
        db,
        days=days,
        symbol=conf.symbol if conf else None,
        validated_only=True,
    )
    return {
        "performance": perf,
        "alerts_paused": bool(getattr(conf, "alerts_paused", False)) if conf else False,
        "pause_reason": getattr(conf, "pause_reason", None) if conf else None,
        "symbol": conf.symbol if conf else None,
    }


@router.get("/worker-status")
async def worker_status() -> dict:
    """Latest worker/Deriv connection snapshot for the dashboard."""
    return get_latest_status()


@router.post("/worker-status")
async def push_worker_status(
    payload: dict,
    _: None = Depends(require_internal_secret),
) -> dict:
    """Worker → API status publish (requires X-Internal-Secret)."""
    await send_status_update(payload)
    return {"status": "ok"}


@router.post("/worker-event")
async def push_worker_event(
    payload: dict,
    _: None = Depends(require_internal_secret),
) -> dict:
    """Worker → API browser notification publish (requires X-Internal-Secret)."""
    event_type = payload.get("type", "signal_alert")
    data = payload.get("data") or payload
    if event_type == "signal_alert":
        await send_signal_notification(data)
    else:
        await send_status_update(data)
    return {"status": "ok"}
