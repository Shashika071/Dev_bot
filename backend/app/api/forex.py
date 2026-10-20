"""Dedicated Forex controls. Scan while stopped is analysis only."""
from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field

from app.config import settings
from app.forex.jobs import forex_jobs
from app.forex.service import forex_service
from app.trade_prefs import load_trade_prefs, save_trade_prefs, save_trade_token

router = APIRouter(prefix="/forex", tags=["forex"])


class StartBody(BaseModel):
    symbols: list[str] | None = None
    duration_seconds: int | None = Field(None, ge=14400, le=14400)
    stake: float | None = Field(None, ge=0.35, le=10)
    demo_auto_trade: bool | None = None


class AccountBody(BaseModel):
    token: str | None = Field(None, min_length=8, max_length=256)
    app_id: str | None = Field(None, max_length=64)
    enabled: bool


def require_forex_control(x_forex_control_token: str | None = Header(default=None)) -> None:
    """Optional shared-secret gate for mutating Forex controls.

    When FOREX_CONTROL_TOKEN is unset, local/dev stays open. Set the env var
    before exposing the API beyond a trusted network.
    """
    expected = (settings.forex_control_token or "").strip()
    if not expected:
        return
    if not x_forex_control_token or x_forex_control_token.strip() != expected:
        raise HTTPException(401, "Forex control token required")


@router.get("/status")
async def status():
    return forex_service.status()


@router.get("/symbols")
async def symbols():
    return await forex_service.symbols()


@router.get("/chart")
async def chart(
    symbol: str = Query("frxEURUSD"),
    timeframe: str = Query("15m"),
    count: int = Query(0, ge=0, le=2500),
):
    try:
        return await forex_service.chart(symbol, timeframe=timeframe, count=count or None)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"Chart feed unavailable ({type(exc).__name__}: {exc})") from exc


@router.get("/history/status")
async def history_status():
    return forex_jobs.snapshot()["history"]


@router.post("/history/download")
async def history_download(x_forex_control_token: str | None = Header(default=None)):
    require_forex_control(x_forex_control_token)
    return (await forex_jobs.start_history())["history"]


@router.get("/train/status")
async def train_status():
    return forex_jobs.snapshot()["train"]


@router.post("/train")
async def train(x_forex_control_token: str | None = Header(default=None)):
    require_forex_control(x_forex_control_token)
    try:
        return (await forex_jobs.start_train())["train"]
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/scan")
async def scan(x_forex_control_token: str | None = Header(default=None)):
    require_forex_control(x_forex_control_token)
    return await forex_service.scan()


@router.post("/start")
async def start(body: StartBody, x_forex_control_token: str | None = Header(default=None)):
    require_forex_control(x_forex_control_token)
    try:
        return await forex_service.start(body.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/stop")
async def stop(x_forex_control_token: str | None = Header(default=None)):
    require_forex_control(x_forex_control_token)
    return await forex_service.stop()


@router.put("/account")
async def account(body: AccountBody, x_forex_control_token: str | None = Header(default=None)):
    require_forex_control(x_forex_control_token)
    if body.token:
        save_trade_token(body.token)
    prefs = load_trade_prefs()
    updates = {"server_execution_allowed": body.enabled}
    if body.app_id is not None:
        updates["deriv_app_id"] = body.app_id.strip()
    save_trade_prefs({**prefs, **updates})
    if not body.enabled:
        await forex_service.stop()
    return forex_service.status()
