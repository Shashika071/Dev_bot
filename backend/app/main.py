"""
Main FastAPI application — Volatility 100 and legacy Forex Rise/Fall.
"""

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.forex import router as forex_router
from app.forex.service import forex_service
from app.api.synthetic import router as synthetic_router
from app.synthetic.service import synthetic_service
from app.config import settings
from app.database import init_db

logger = structlog.get_logger(__name__)

app = FastAPI(
    title="Deriv Volatility 100 Rise/Fall Bot",
    description="FRAMA / Range Filter confluence, CatBoost validation and optional demo execution.",
    version="3.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list or ["http://localhost:5173"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(forex_router)
app.include_router(synthetic_router)


@app.on_event("startup")
async def startup():
    logger.info("app_startup", bot_mode=settings.bot_mode)
    await init_db()
    await forex_service.resume()
    await synthetic_service.resume()


@app.on_event("shutdown")
async def shutdown():
    await forex_service.shutdown()
    await synthetic_service.shutdown()


@app.get("/health")
async def health_check():
    return {"status": "ok", "product": f"{settings.bot_mode}_rise_fall"}
