"""Volatility 100 controls; manual scans while stopped cannot buy."""
from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.forex import AccountBody, require_forex_control
from app.synthetic.jobs import synthetic_jobs
from app.synthetic.service import synthetic_service
from app.trade_prefs import load_trade_prefs, save_trade_prefs, save_trade_token

router = APIRouter(prefix="/synthetic", tags=["synthetic"])


class StartBody(BaseModel):
    stake: float | None = Field(None, ge=0.35, le=10)
    duration_seconds: int | None = Field(None, ge=300, le=300)
    symbols: list[str] | None = None
    demo_auto_trade: bool = False
    max_signals_per_day: int | None = Field(None, ge=1, le=100)


class HistoryBody(BaseModel):
    days: int = Field(90, ge=60, le=90)


@router.get("/status")
async def status():
    return synthetic_service.status()


@router.get("/symbols")
async def symbols():
    return await synthetic_service.symbols()


@router.get("/quote")
async def quote():
    return synthetic_service.quote()


@router.get("/chart")
async def chart(timeframe: str = Query("5m"), count: int = Query(500, ge=240, le=1000)):
    try:
        return await synthetic_service.chart(timeframe, count)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"Chart feed unavailable ({type(exc).__name__})") from exc


@router.post("/scan")
async def scan(x_forex_control_token: str | None = Header(default=None)):
    require_forex_control(x_forex_control_token)
    return await synthetic_service.scan()


@router.post("/start")
async def start(body: StartBody, x_forex_control_token: str | None = Header(default=None)):
    require_forex_control(x_forex_control_token)
    try:
        return await synthetic_service.start(body.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/stop")
async def stop(x_forex_control_token: str | None = Header(default=None)):
    require_forex_control(x_forex_control_token)
    return await synthetic_service.stop()


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
        await synthetic_service.stop()
    return synthetic_service.status()


@router.get("/history/status")
async def history_status():
    return synthetic_jobs.snapshot()["history"]


@router.post("/history/download")
async def history_download(body: HistoryBody | None = None, x_forex_control_token: str | None = Header(default=None)):
    require_forex_control(x_forex_control_token)
    try:
        return (await synthetic_jobs.start_history(days=(body or HistoryBody()).days))["history"]
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/train/status")
async def train_status():
    return synthetic_jobs.snapshot()["train"]


@router.post("/train")
async def train(x_forex_control_token: str | None = Header(default=None)):
    require_forex_control(x_forex_control_token)
    try:
        return (await synthetic_jobs.start_train())["train"]
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
