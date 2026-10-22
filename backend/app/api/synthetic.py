"""Volatility 100 controls; manual scans while stopped cannot buy."""
import asyncio

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.forex import AccountBody, require_forex_control
from app.config import settings
from app.synthetic.jobs import synthetic_jobs
from app.synthetic.service import synthetic_service
from app.trading_ml.service import record_paper, start_research, start_score, start_ticks
from app.trade_prefs import load_trade_prefs, save_trade_prefs, save_trade_token

router = APIRouter(prefix="/synthetic", tags=["synthetic"])


class StartBody(BaseModel):
    stake: float | None = Field(None, ge=0.35, le=10)
    duration_seconds: int | None = Field(None, ge=60, le=60)
    symbols: list[str] | None = None
    demo_auto_trade: bool = False
    max_signals_per_day: int | None = Field(None, ge=1, le=100)


class HistoryBody(BaseModel):
    days: int = Field(90, ge=60, le=90)


class ResearchBody(BaseModel):
    duration_seconds: int = Field(300, ge=60, le=3600)
    payout: float | None = None
    models: list[str] | None = None


class ScoreBody(BaseModel):
    payout: float | None = None


class TickBody(BaseModel):
    hours: int = Field(6, ge=1, le=24)


@router.get("/status")
async def status():
    synthetic_service._ensure_confidence()
    return await asyncio.to_thread(synthetic_service.status)


@router.get("/symbols")
async def symbols():
    return await synthetic_service.symbols()


@router.get("/quote")
async def quote():
    return synthetic_service.quote()


@router.get("/confidence")
async def confidence():
    return synthetic_service.confidence()


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
    return await asyncio.to_thread(synthetic_service.status)


@router.get("/history/status")
async def history_status():
    return (await asyncio.to_thread(synthetic_jobs.snapshot))["history"]


@router.post("/history/download")
async def history_download(body: HistoryBody | None = None, x_forex_control_token: str | None = Header(default=None)):
    require_forex_control(x_forex_control_token)
    try:
        return (await synthetic_jobs.start_history(days=(body or HistoryBody()).days))["history"]
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/train/status")
async def train_status():
    return (await asyncio.to_thread(synthetic_jobs.snapshot))["train"]


@router.post("/train")
async def train(x_forex_control_token: str | None = Header(default=None)):
    require_forex_control(x_forex_control_token)
    raise HTTPException(400, "The previous CatBoost model was removed. Use the research signal engine.")


@router.post("/research/evaluate")
async def research_evaluate(body: ResearchBody, x_forex_control_token: str | None = Header(default=None)):
    require_forex_control(x_forex_control_token)
    try:
        return await start_research(
            settings.model_dir,
            duration_seconds=body.duration_seconds, payout=body.payout, models=body.models,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/research/score")
async def research_score(body: ScoreBody | None = None, x_forex_control_token: str | None = Header(default=None)):
    require_forex_control(x_forex_control_token)
    try:
        return await start_score(
            settings.model_dir,
            payout=None if body is None else body.payout,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/research/paper")
async def research_paper(x_forex_control_token: str | None = Header(default=None)):
    require_forex_control(x_forex_control_token)
    try:
        quote = synthetic_service.quote()
        return await asyncio.to_thread(
            record_paper,
            settings.model_dir,
            quote_epoch=quote.get("epoch") if isinstance(quote, dict) else None,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/research/ticks")
async def research_ticks(body: TickBody | None = None, x_forex_control_token: str | None = Header(default=None)):
    require_forex_control(x_forex_control_token)
    try:
        return await start_ticks(
            settings.model_dir,
            hours=(body or TickBody()).hours,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
