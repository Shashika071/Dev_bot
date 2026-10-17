"""
Main FastAPI application entrypoint.
"""

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import setup, signals, train, ws
from app.api.analysis import router as analysis_router
from app.api.market import router as market_router
from app.api.accuracy import router as accuracy_router, service as accuracy_service
from app.config import settings
from app.database import init_db

logger = structlog.get_logger(__name__)

app = FastAPI(
    title="Deriv Touch Signal Bot",
    description="Touch and no-touch contract signal research.",
    version="1.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list or ["http://localhost:5173"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(setup.router)
app.include_router(signals.router)
app.include_router(ws.router)
app.include_router(train.router)
app.include_router(market_router)
app.include_router(analysis_router)
app.include_router(accuracy_router)


@app.on_event("startup")
async def startup():
    logger.info("app_startup")
    await init_db()
    # Resume Analyze/Force watcher if it was running before API restart
    try:
        from app.signal_engine.analyze_watch import analyze_watch

        await analyze_watch.resume_if_needed()
    except Exception as e:
        logger.warning("analyze_watch_resume_skip", error=str(e))
    # Buy-capable session stays down unless the server execution gate is open.
    try:
        from app.execution.session_ctl import start_if_allowed

        if await start_if_allowed():
            logger.info("trade_account_session_started")
        else:
            logger.info("trade_account_session_not_started", reason="execution_gate_closed")
    except Exception as e:
        logger.warning("trade_account_session_start_skip", error=str(e))


@app.on_event("shutdown")
async def shutdown():
    await accuracy_service.stop()
    try:
        from app.execution.session_ctl import stop_session

        await stop_session()
    except Exception as e:
        logger.warning("trade_account_session_stop_skip", error=str(e))


@app.get("/health")
async def health_check():
    return {"status": "ok"}
