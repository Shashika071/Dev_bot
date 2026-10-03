"""
Main FastAPI application entrypoint.
"""

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import setup, signals, train, ws
from app.config import settings
from app.database import init_db

logger = structlog.get_logger(__name__)

app = FastAPI(
    title="Deriv Touch Signal Bot",
    description="Quantitative ML-powered signal bot (No auto-trading).",
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


@app.on_event("startup")
async def startup():
    logger.info("app_startup")
    await init_db()


@app.get("/health")
async def health_check():
    return {"status": "ok"}
