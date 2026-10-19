"""Dedicated Forex controls. Scan while stopped is analysis only."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.forex.service import forex_service
from app.trade_prefs import load_trade_prefs, save_trade_prefs, save_trade_token

router = APIRouter(prefix="/forex", tags=["forex"])


class StartBody(BaseModel):
    symbols: list[str] | None = None
    # Product lock: four-hour Rise/Fall only. Other values are rejected in validate_config.
    duration_seconds: int | None = Field(None, ge=14400, le=14400)
    stake: float | None = Field(None, ge=0.35, le=10)
    demo_auto_trade: bool | None = None


class AccountBody(BaseModel):
    token: str | None = Field(None, min_length=8, max_length=256)
    app_id: str | None = Field(None, max_length=64)
    enabled: bool


@router.get("/status")
async def status():
    return forex_service.status()


@router.get("/symbols")
async def symbols():
    return await forex_service.symbols()


@router.post("/scan")
async def scan():
    return await forex_service.scan()


@router.post("/start")
async def start(body: StartBody):
    try:
        return await forex_service.start(body.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/stop")
async def stop():
    return await forex_service.stop()


@router.put("/account")
async def account(body: AccountBody):
    if body.token:
        save_trade_token(body.token)
    prefs = load_trade_prefs()
    updates = {"server_execution_allowed": body.enabled}
    if body.app_id is not None:
        updates["deriv_app_id"] = body.app_id.strip()
    # This page never opts into the legacy Touch automatic-trade controls.
    save_trade_prefs({**prefs, **updates})
    if not body.enabled:
        await forex_service.stop()
    return forex_service.status()
