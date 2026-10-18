"""Probability-focused signal mode. Optional auto-trade uses Setup trade prefs."""
from __future__ import annotations

import asyncio
import glob
import os
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.accuracy.service import AccuracyService
from app.config import settings
from app.trade_prefs import load_trade_prefs, save_trade_prefs

router = APIRouter(prefix="/accuracy", tags=["accuracy signals"])
service = AccuracyService(Path(settings.model_dir).parent / "accuracy")
logger = structlog.get_logger(__name__)

_train_state: dict[str, Any] = {
    "status": "idle",  # idle | running | done | error
    "progress": "",
    "result": None,
    "error": None,
}


class AccuracyTradeBody(BaseModel):
    accuracy_auto_trade_enabled: bool = False


class AccuracyCooldownBody(BaseModel):
    # Optional / loose type — clamp in the handler so empty UI edits don't 422.
    accuracy_cooldown_seconds: int | float | None = Field(5760)


class AccuracyTrainBody(BaseModel):
    profile: str = Field("technical_v2", pattern="^(compact_v1|technical_v2)$")
    activate: bool = True


def _find_tick_parquet(symbol: str = "R_100") -> Path | None:
    """Prefer staged parquet under /app/data/parquet."""
    root = settings.parquet_data_dir
    if not root or not os.path.isdir(root):
        return None
    paths = sorted(
        glob.glob(os.path.join(root, "**", "*.parquet"), recursive=True),
        key=lambda p: os.path.getmtime(p),
        reverse=True,
    )
    preferred = [p for p in paths if str(symbol) in os.path.basename(p)]
    for path in preferred or paths:
        try:
            if os.path.getsize(path) > 0:
                return Path(path)
        except OSError:
            continue
    return None


@router.get("/status")
async def status():
    out = service.status()
    out["train"] = {
        "status": _train_state["status"],
        "progress": _train_state["progress"],
        "error": _train_state["error"],
        "result": _train_state["result"],
    }
    return out


@router.post("/start")
async def start():
    try:
        return await service.start()
    except (FileNotFoundError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=409, detail=f"Train the accuracy profile first: {exc}") from exc


@router.post("/stop")
async def stop():
    return await service.stop()


@router.post("/reset-signals")
async def reset_signals():
    """Clear Forward signals history and reset daily/cooldown slots (model kept)."""
    out = await service.reset_signals()
    out["train"] = {
        "status": _train_state["status"],
        "progress": _train_state["progress"],
        "error": _train_state["error"],
        "result": _train_state["result"],
    }
    return out


@router.put("/auto-trade")
async def set_accuracy_auto_trade(body: AccuracyTradeBody):
    """Toggle Touch Accuracy auto-trade (still needs Setup auto-trade gates)."""
    prefs = load_trade_prefs()
    prefs["accuracy_auto_trade_enabled"] = bool(body.accuracy_auto_trade_enabled)
    saved = save_trade_prefs(prefs)
    return {
        "accuracy_auto_trade_enabled": bool(saved.get("accuracy_auto_trade_enabled")),
        "auto_trade_enabled": bool(saved.get("auto_trade_enabled")),
        "execution_confirmed": bool(saved.get("execution_confirmed")),
        "message": (
            "Accuracy auto-trade on. Orders still need Setup auto-trade + confirmation + server EXECUTION_ALLOWED."
            if saved.get("accuracy_auto_trade_enabled")
            else "Accuracy auto-trade off — signals only."
        ),
        "status": service.status(),
    }


@router.put("/cooldown")
async def set_accuracy_cooldown(body: AccuracyCooldownBody):
    """Set Touch Accuracy market-rest cooldown between signals (min 600s)."""
    if body.accuracy_cooldown_seconds is None:
        raw = 5760
    else:
        try:
            raw = int(float(body.accuracy_cooldown_seconds))
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=422,
                detail="Cooldown must be a number of seconds (600–7200).",
            ) from None
    cool = max(600, min(7200, raw))
    prefs = load_trade_prefs()
    prefs["accuracy_cooldown_seconds"] = cool
    saved = save_trade_prefs(prefs)
    cool = int(saved.get("accuracy_cooldown_seconds", cool))
    service.apply_cooldown(cool)
    return {
        "accuracy_cooldown_seconds": cool,
        "message": f"Accuracy cooldown set to {cool}s ({cool / 60:.0f} min between signals).",
        "status": service.status(),
    }


@router.get("/train-status")
async def train_status():
    return dict(_train_state)


async def _run_accuracy_train(ticks: Path, output: Path, profile: str, activate: bool) -> None:
    global _train_state
    from app.accuracy.training import train

    loop = asyncio.get_event_loop()

    def _progress(msg: str) -> None:
        global _train_state
        _train_state = {**_train_state, "status": "running", "progress": msg}

    _progress(f"Training {profile} from {ticks.name}…")
    try:
        # train() is CPU-heavy; keep the API responsive.
        def _job():
            _progress("Fitting / calibrating / selecting (this can take a while)…")
            return train(ticks, output, feature_profile=profile, activate=activate)

        result = await loop.run_in_executor(None, _job)
        selected = result.get("selection") if isinstance(result, dict) else None
        run_id = result.get("run_id") if isinstance(result, dict) else None
        _train_state = {
            "status": "done",
            "progress": "Training complete.",
            "result": {
                "run_id": run_id,
                "selection": selected,
                "profile": profile,
                "activated": activate,
                "ticks_file": str(ticks),
                "selected_model": (selected or {}).get("model") if isinstance(selected, dict) else None,
                "threshold": (selected or {}).get("threshold") if isinstance(selected, dict) else None,
            },
            "error": None,
        }
        logger.info("accuracy_train_done", profile=profile, selection=selected)
    except Exception as exc:
        logger.exception("accuracy_train_failed", error=str(exc))
        _train_state = {
            "status": "error",
            "progress": "",
            "result": None,
            "error": str(exc),
        }


@router.post("/train")
async def start_accuracy_train(body: AccuracyTrainBody | None = None):
    """
    Train / retrain the Touch Accuracy profile from staged parquet.
    Does not change the Dashboard ML models — only data/accuracy artifacts.
    """
    global _train_state
    if _train_state.get("status") == "running":
        raise HTTPException(status_code=409, detail="Accuracy training already in progress.")
    if service.task and not service.task.done():
        raise HTTPException(
            status_code=409,
            detail="Stop accuracy signals first, then train.",
        )

    body = body or AccuracyTrainBody()
    ticks = _find_tick_parquet("R_100")
    if ticks is None:
        raise HTTPException(
            status_code=409,
            detail="No staged parquet found. Stage SQLite / export history on the Train page first.",
        )
    output = Path(settings.model_dir).parent / "accuracy"
    _train_state = {
        "status": "running",
        "progress": f"Queued {body.profile} training on {ticks.name}…",
        "result": None,
        "error": None,
    }
    asyncio.create_task(
        _run_accuracy_train(ticks, output, body.profile, bool(body.activate))
    )
    return {
        "status": "started",
        "message": f"Training Touch Accuracy ({body.profile}). Poll /accuracy/train-status.",
        "ticks_file": str(ticks),
        "profile": body.profile,
        "activate": body.activate,
    }
