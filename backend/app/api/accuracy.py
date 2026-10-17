"""Probability-focused signal mode. Optional auto-trade uses Setup trade prefs."""
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.accuracy.service import AccuracyService
from app.config import settings
from app.trade_prefs import load_trade_prefs, save_trade_prefs

router=APIRouter(prefix="/accuracy",tags=["accuracy signals"])
service=AccuracyService(Path(settings.model_dir).parent/"accuracy")


class AccuracyTradeBody(BaseModel):
    accuracy_auto_trade_enabled: bool = False


@router.get("/status")
async def status():
    return service.status()


@router.post("/start")
async def start():
    try:
        return await service.start()
    except (FileNotFoundError,ValueError,KeyError) as exc:
        raise HTTPException(status_code=409,detail=f"Train the accuracy profile first: {exc}") from exc


@router.post("/stop")
async def stop():
    return await service.stop()


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
