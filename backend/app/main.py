"""
Main FastAPI application entrypoint.
"""

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import digitmatch, setup, signals, train, ws
from app.config import settings
from app.database import init_db

logger = structlog.get_logger(__name__)

app = FastAPI(
    title="Deriv Touch Signal Bot",
    description="Touch signals plus a separate demo-only Digit Matches research desk.",
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
app.include_router(digitmatch.router)


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
    # Event-driven trade account (balance stream) — one PAT session
    try:
        from app.deriv.trade_session import trade_account_session
        from app.trade_prefs import token_status

        if token_status().get("token_configured"):
            await trade_account_session.start()
            logger.info("trade_account_session_started")
    except Exception as e:
        logger.warning("trade_account_session_start_skip", error=str(e))


@app.on_event("shutdown")
async def shutdown():
    try:
        from app.deriv.trade_session import trade_account_session

        await trade_account_session.stop()
    except Exception as e:
        logger.warning("trade_account_session_stop_skip", error=str(e))


@app.get("/health")
async def health_check():
    return {"status": "ok"}
