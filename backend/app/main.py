"""
Main FastAPI application — Forex Rise/Fall only.
"""

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.forex import router as forex_router
from app.forex.service import forex_service
from app.config import settings
from app.database import init_db

logger = structlog.get_logger(__name__)

app = FastAPI(
    title="Deriv Forex Rise/Fall Bot",
    description="Multi-timeframe Forex analysis and demo-only Rise/Fall execution.",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list or ["http://localhost:5173"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(forex_router)


@app.on_event("startup")
async def startup():
    logger.info("app_startup", bot_mode=settings.bot_mode)
    await init_db()
    if settings.bot_mode != "forex":
        logger.warning("bot_mode_not_forex", bot_mode=settings.bot_mode,
                       hint="Set BOT_MODE=forex; legacy Touch routes are removed from this API")
    await forex_service.resume()


@app.on_event("shutdown")
async def shutdown():
    await forex_service.shutdown()


@app.get("/health")
async def health_check():
    return {"status": "ok", "product": "forex_rise_fall"}
