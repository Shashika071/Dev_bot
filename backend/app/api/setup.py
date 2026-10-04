"""
Setup and configuration API endpoints.
Provides contract discovery and requires explicit confirmation before alerts activate.
"""

import asyncio
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Any

from app.database import get_db
from app.models.settings import ContractSettings
from app.deriv.client import DerivWSClient
from app.collector.discovery import ContractDiscovery
from app.ops_prefs import load_ops_prefs, save_ops_prefs
from app.trade_prefs import (
    clear_trade_token,
    load_trade_prefs,
    save_trade_prefs,
    save_trade_token,
    trade_prefs_public,
)

router = APIRouter(prefix="/setup", tags=["setup"])


class OpsPrefsBody(BaseModel):
    max_signals_per_day: int = Field(3, ge=0, le=10)
    signal_cooldown_seconds: int = Field(3600, ge=0, le=7200)
    manual_min_confidence: float = Field(0.95, ge=0.5, le=0.99)
    manual_min_margin_over_breakeven: float = Field(0.03, ge=0.0, le=0.5)
    min_ev_margin: float = Field(0.02, ge=0.0, le=0.5)
    min_calibration_samples: int = Field(50, ge=5, le=200)
    require_touch_confluence: bool = True
    confluence_min_score: float = Field(5.0, ge=0.0, le=50.0)
    confluence_min_gap: float = Field(1.5, ge=0.0, le=20.0)
    require_candle_confirm: bool = True
    candle_confirm_min_score: float = Field(4.0, ge=0.0, le=20.0)
    candle_confirm_min_gap: float = Field(1.0, ge=0.0, le=10.0)
    auto_pause_enabled: bool = True
    auto_pause_min_resolved: int = Field(20, ge=5, le=200)
    auto_pause_ci_margin: float = Field(0.0, ge=-0.1, le=0.1)


@router.get("/ops-prefs")
async def get_ops_prefs() -> dict:
    """Day-to-day bot gates owned by the UI (.env is default only)."""
    prefs = load_ops_prefs()
    return {
        **prefs,
        "env_is_default_only": True,
        "ui_owned": True,
        "never_edit_in_env": [
            "max_signals_per_day",
            "signal_cooldown_seconds",
            "manual_min_confidence",
            "manual_min_margin_over_breakeven",
            "min_ev_margin",
            "min_calibration_samples",
            "require_touch_confluence",
            "confluence_min_score",
            "confluence_min_gap",
            "require_candle_confirm",
            "candle_confirm_min_score",
            "candle_confirm_min_gap",
            "auto_pause_enabled",
            "auto_pause_min_resolved",
            "auto_pause_ci_margin",
        ],
        "stay_in_env": [
            "POSTGRES_*",
            "DATABASE_URL",
            "SECRET_KEY",
            "INTERNAL_API_SECRET",
            "VITE_*",
            "CORS_ORIGINS",
            "DERIV_HTTPS_PORT",
            "CERTBOT_EMAIL",
        ],
        "guide": {
            "max_signals_per_day": "Max validated signals per Asia/Colombo day (3 = default).",
            "signal_cooldown_seconds": "Min seconds between signals (3600 = 1 hour market rest).",
            "manual_min_confidence": "Analyze & Signal needs this calibrated touch probability (0.95 = 95%).",
            "manual_min_margin_over_breakeven": "Analyze also needs this much above quote breakeven.",
            "min_ev_margin": "Live EV gate margin over breakeven for auto alerts.",
            "min_calibration_samples": "Live EV needs this many nearby calibration samples.",
            "require_touch_confluence": "If on, only touch_confluence strategy can alert.",
            "confluence_min_score": "Minimum confluence score to fire.",
            "confluence_min_gap": "Min score gap between upper vs lower confluence.",
            "require_candle_confirm": (
                "If on, 1m/5m/15m candles + patterns must agree "
                "(engulfing, stars, harami, tweezers, soldiers/crows, pins, etc.)."
            ),
            "candle_confirm_min_score": "Minimum candle confirmation score (multi-TF + patterns).",
            "candle_confirm_min_gap": "Min score gap vs opposite candle direction.",
            "auto_pause_enabled": "Pause alerts if live results look worse than breakeven.",
            "auto_pause_min_resolved": "How many resolved live signals before auto-pause can fire.",
            "auto_pause_ci_margin": "Extra margin vs mean breakeven for the pause rule.",
        },
    }


@router.put("/ops-prefs")
async def put_ops_prefs(body: OpsPrefsBody) -> dict:
    """Save bot gates from UI — no VPS/.env edit needed after deploy."""
    saved = save_ops_prefs(body.model_dump())
    return {
        **saved,
        "status": "saved",
        "env_is_default_only": True,
        "message": "Ops prefs saved. Live worker/backend pick them up on the next check (no recreate needed).",
    }


class TradePrefsBody(BaseModel):
    auto_trade_enabled: bool = False
    trade_stake: float = Field(1.0, ge=0.35, le=10000.0)
    force_min_probability: float = Field(0.80, ge=0.50, le=0.99)
    trade_currency: str = Field("USD", min_length=1, max_length=8)
    deriv_app_id: str = Field("", max_length=64)
    cross_barrier_enabled: bool = False
    model_barrier_distance: float = Field(0.9, ge=0.01, le=50.0)
    cross_barrier_min_probability: float = Field(0.80, ge=0.50, le=0.99)
    cross_barrier_require_candles: bool = True


class TradeTokenBody(BaseModel):
    token: str = Field(..., min_length=8, max_length=256)


@router.get("/trade-prefs")
async def get_trade_prefs() -> dict:
    """Auto-trade toggles + token status (never returns full token)."""
    pub = trade_prefs_public()
    return {
        **pub,
        "warning": (
            "Auto-trade can spend real or demo money on Deriv. "
            "Enable only with a token that has trade scope. Default is OFF."
        ),
        "guide": {
            "auto_trade_enabled": "When on, Analyze / Force signal may place a buy after a signal is created.",
            "trade_stake": "Stake amount sent in the proposal/buy (your account currency).",
            "force_min_probability": "Force (model + candles) mode min calibrated probability (e.g. 0.8 = 80%).",
            "trade_currency": "Currency for proposal (usually USD).",
            "deriv_app_id": "App ID from developers.deriv.com → Apps (must match your PAT app).",
            "cross_barrier_enabled": "Show Cross-barrier watch on Dashboard (score far model, trade Setup barrier).",
            "model_barrier_distance": "Barrier the trained model used (e.g. 0.9). Features/score use this.",
            "cross_barrier_min_probability": "Step confidence on the model barrier before trading Setup (e.g. 0.8 = 80%).",
            "cross_barrier_require_candles": "When on, Cross-barrier also needs 1m/5m candle confirm on the trade direction.",
        },
    }


@router.put("/trade-prefs")
async def put_trade_prefs(body: TradePrefsBody) -> dict:
    saved = save_trade_prefs(body.model_dump())
    return {
        **saved,
        **trade_prefs_public(),
        "status": "saved",
        "message": "Trade prefs saved. Auto-trade stays off unless enabled and a token is set.",
    }


async def _fetch_trade_account() -> dict:
    """Authorize with stored token and return balance / profit summary (hard timeout)."""
    from app.deriv.trade_client import DerivTradeClient
    from app.trade_prefs import get_trade_token, resolve_deriv_app_id

    token = get_trade_token()
    if not token:
        return {"ok": False, "error": "No trade token configured", "token_configured": False}

    app_id = resolve_deriv_app_id()
    client = DerivTradeClient(token, app_id=app_id or None)

    async def _run() -> dict:
        summary = await client.fetch_account_summary()
        summary["token_configured"] = True
        summary["app_id_used"] = app_id
        try:
            import json
            import os
            from app.config import settings

            path = os.path.join(settings.model_dir, "deriv_trade_account.json")
            os.makedirs(settings.model_dir, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(summary, f, indent=2, default=str)
        except Exception:
            pass
        return summary

    try:
        return await asyncio.wait_for(_run(), timeout=22.0)
    except asyncio.TimeoutError:
        return {
            "ok": False,
            "error": "Account lookup timed out (22s).",
            "token_configured": True,
            "app_id_used": app_id,
        }
    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
            "token_configured": True,
            "app_id_used": app_id,
        }
    finally:
        try:
            await client.close()
        except Exception:
            pass


@router.get("/trade-account")
async def get_trade_account(refresh: bool = True) -> dict:
    """
    Show Deriv account details for the saved trade token:
    loginid, demo/real, balance, today profit, recent P/L.
    """
    from app.trade_prefs import token_status

    status = token_status()
    if not status.get("token_configured"):
        return {"ok": False, "token_configured": False, "error": "No trade token configured"}

    if not refresh:
        try:
            import json
            import os
            from app.config import settings

            path = os.path.join(settings.model_dir, "deriv_trade_account.json")
            if os.path.isfile(path):
                with open(path, encoding="utf-8") as f:
                    cached = json.load(f)
                if isinstance(cached, dict):
                    return {**cached, "cached": True, **status}
        except Exception:
            pass

    summary = await _fetch_trade_account()
    return {**summary, **status}


@router.put("/trade-token")
async def put_trade_token(body: TradeTokenBody) -> dict:
    try:
        meta = save_trade_token(body.token)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    # Immediately authorize and return account details
    account = await _fetch_trade_account()
    return {
        **meta,
        **load_trade_prefs(),
        "status": "saved",
        "message": "Token encrypted and stored. Full token is never returned to the UI.",
        "account": account,
    }


@router.delete("/trade-token")
async def delete_trade_token() -> dict:
    clear_trade_token()
    # Hard safety: clearing token also disables auto-trade
    prefs = load_trade_prefs()
    if prefs.get("auto_trade_enabled"):
        prefs = save_trade_prefs({**prefs, "auto_trade_enabled": False})
    # Clear cached account snapshot
    try:
        import os
        from app.config import settings

        path = os.path.join(settings.model_dir, "deriv_trade_account.json")
        if os.path.isfile(path):
            os.remove(path)
    except Exception:
        pass
    return {
        "token_configured": False,
        "token_mask": None,
        **prefs,
        "status": "cleared",
        "message": "Trade token removed. Auto-trade disabled.",
    }


# Global Deriv client for API discovery requests
_deriv_client = DerivWSClient()
_connect_lock: asyncio.Lock | None = None


def _lock() -> asyncio.Lock:
    global _connect_lock
    if _connect_lock is None:
        _connect_lock = asyncio.Lock()
    return _connect_lock


async def _ready_client(*, force: bool = False) -> DerivWSClient:
    """Ensure the shared discovery client has a live Deriv connection."""
    async with _lock():
        try:
            if force:
                await _deriv_client.force_reconnect()
            else:
                await _deriv_client.ensure_connected()
        except Exception as e:
            raise HTTPException(
                status_code=503,
                detail=f"Deriv API unavailable: {e}",
            ) from e
    if not _deriv_client.connected:
        raise HTTPException(status_code=503, detail="Deriv API not connected")
    return _deriv_client


@router.on_event("startup")
async def startup_event():
    asyncio.create_task(_deriv_client.connect())


@router.on_event("shutdown")
async def shutdown_event():
    await _deriv_client.disconnect()


@router.get("/discover/symbols")
async def discover_symbols() -> list[dict]:
    """Fetch Volatility 100 instruments to confirm correct symbol."""
    client = await _ready_client()
    discovery = ContractDiscovery(client)
    try:
        return await discovery.discover_symbols()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Symbol discovery failed: {e}") from e


@router.get("/discover/contracts/{symbol}")
async def discover_contracts(symbol: str) -> dict:
    """Check Touch contract availability for a symbol."""
    client = await _ready_client()
    discovery = ContractDiscovery(client)
    try:
        return await discovery.discover_contracts(symbol)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Contract discovery failed: {e}") from e


@router.post("/discover/quote")
async def sample_quote(
    symbol: str,
    barrier: str,
    duration: int = 9,
    duration_unit: str = "m",
    direction: str = "both",
) -> dict:
    """Get sample quote(s) to verify barrier interpretation (upper/lower/both)."""
    from app.deriv.barriers import iter_direction_barriers

    client = await _ready_client()
    discovery = ContractDiscovery(client)
    quotes = []

    for dir_name, signed_barrier in iter_direction_barriers(barrier, direction):
        result = await discovery.get_sample_quote(
            symbol=symbol,
            barrier=signed_barrier,
            duration=duration,
            duration_unit=duration_unit,
        )
        err = str(result.get("error", ""))
        if not result.get("success") and ("timeout" in err.lower() or "timed out" in err.lower()):
            client = await _ready_client(force=True)
            discovery = ContractDiscovery(client)
            result = await discovery.get_sample_quote(
                symbol=symbol,
                barrier=signed_barrier,
                duration=duration,
                duration_unit=duration_unit,
            )
        if not result.get("success"):
            err = str(result.get("error", "Quote failed"))
            transient = ("timeout", "timed out", "not connected", "unavailable", "rejected", "reconnected")
            status = 503 if any(k in err.lower() for k in transient) else 400
            raise HTTPException(status_code=status, detail=f"{dir_name}: {err}")
        quotes.append({**result, "direction": dir_name, "barrier": signed_barrier})

    # Backward-compatible top-level fields from the first quote
    first = quotes[0]
    return {
        "success": True,
        "direction_mode": direction,
        "quotes": quotes,
        "ask_price": first.get("ask_price"),
        "payout": first.get("payout"),
        "spot": first.get("spot"),
        "longcode": first.get("longcode"),
    }


@router.get("/current")
async def get_current_settings(db: AsyncSession = Depends(get_db)) -> Any:
    """Get currently confirmed contract settings."""
    result = await db.execute(
        select(ContractSettings)
        .order_by(ContractSettings.id.desc())
        .limit(1)
    )
    settings = result.scalar_one_or_none()
    if not settings:
        return {}
    
    return {
        "symbol": settings.symbol,
        "display_name": settings.display_name,
        "contract_type": settings.contract_type,
        "duration_value": settings.duration_value,
        "duration_unit": settings.duration_unit,
        "barrier_input": settings.barrier_input,
        "barrier_direction": getattr(settings, "barrier_direction", "both"),
    }


@router.post("/confirm")
async def confirm_settings(
    settings_data: dict, db: AsyncSession = Depends(get_db)
) -> Any:
    """
    Confirm contract settings.
    Alerts remain disabled until this is explicitly confirmed.
    """
    import datetime
    from sqlalchemy import update
    from app.deriv.barriers import barrier_for_direction, barrier_magnitude

    # Deactivate all previously confirmed settings
    await db.execute(
        update(ContractSettings)
        .where(ContractSettings.is_confirmed == True)
        .values(is_confirmed=False)
    )

    direction = str(settings_data.get("barrier_direction", "both")).lower()
    if direction not in ("upper", "lower", "both"):
        direction = "both"

    # Canonical storage: signed barrier for the primary side; "both" uses +magnitude
    raw_barrier = str(settings_data.get("barrier_input", "0.09"))
    if direction == "lower":
        barrier_input = barrier_for_direction(raw_barrier, "lower")
    else:
        barrier_input = barrier_for_direction(raw_barrier, "upper")

    new_settings = ContractSettings(
        symbol=settings_data.get("symbol", "R_100"),
        display_name=settings_data.get("display_name"),
        contract_type="ONETOUCH",
        duration_value=settings_data.get("duration_value", 9),
        duration_unit=settings_data.get("duration_unit", "m"),
        duration_seconds=settings_data.get("duration_seconds", 540),
        barrier_input=barrier_input,
        barrier_type=settings_data.get("barrier_type", "relative"),
        barrier_direction=direction,
        barrier_unit_description=(
            f"relative_price_points ±{barrier_magnitude(barrier_input)} "
            "(Deriv relative barrier offset from spot, not percent)"
        ),
        is_confirmed=True,
        confirmed_at=datetime.datetime.now(datetime.timezone.utc),
        notes="Confirmed via setup screen",
    )

    db.add(new_settings)
    await db.commit()
    await db.refresh(new_settings)

    return {
        "status": "success",
        "message": "Settings confirmed",
        "symbol": new_settings.symbol,
        "barrier": new_settings.barrier_input,
        "barrier_direction": new_settings.barrier_direction,
    }

