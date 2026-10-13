"""REST market data. Reuses the in-process hub when the worker has published it."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pandas as pd
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.signals import require_internal_secret
from app.config import settings
from app.database import get_db
from app.market_data.hub import market_hub
from app.models.tick import Tick
from app.schemas import MarketSnapshotResponse
from app.signal_engine.chart_candles import INTERVAL_SECONDS, aggregate_ohlc, select_older_candles

router = APIRouter(prefix="/market", tags=["market"])


async def load_candles(
    db: AsyncSession,
    symbol: str,
    interval: str,
    limit: int,
) -> tuple[list[dict], str]:
    if interval not in INTERVAL_SECONDS:
        raise HTTPException(status_code=400, detail=f"Unsupported interval {interval}")
    if market_hub.fresh(symbol, interval, max_age=45):
        rows = market_hub.candles(symbol, interval, limit)
        if len(rows) >= 8:
            return rows, "market_hub"
    step = INTERVAL_SECONDS[interval]
    min_epoch = int(time.time()) - step * (limit + 5)
    result = await db.execute(
        select(Tick.epoch, Tick.quote)
        .where(Tick.symbol == symbol, Tick.epoch >= min_epoch)
        .order_by(Tick.epoch.asc())
    )
    pairs = [(int(epoch), float(quote)) for epoch, quote in result.all()]
    if len(pairs) < 20:
        result = await db.execute(
            select(Tick.epoch, Tick.quote)
            .where(Tick.symbol == symbol)
            .order_by(Tick.epoch.desc())
            .limit(min(20000, limit * max(step, 1)))
        )
        pairs = [(int(epoch), float(quote)) for epoch, quote in reversed(result.all())]
    candles = aggregate_ohlc(pairs, interval, max_candles=limit)
    if candles:
        market_hub.ingest_candles(symbol, interval, candles, source="tick_aggregate")
    return candles, "tick_aggregate"


@router.get("/snapshot", response_model=MarketSnapshotResponse)
async def market_snapshot(symbol: str = "R_100", db: AsyncSession = Depends(get_db)) -> dict:
    if not market_hub.snapshot(symbol).get("last_tick"):
        result = await db.execute(
            select(Tick.epoch, Tick.quote)
            .where(Tick.symbol == symbol)
            .order_by(Tick.epoch.desc())
            .limit(400)
        )
        pairs = [(int(epoch), float(quote)) for epoch, quote in reversed(result.all())]
        if pairs:
            market_hub.ingest_ticks(symbol, pairs)
    return market_hub.snapshot(symbol)


@router.get("/candles")
async def market_candles(
    symbol: str = "R_100",
    interval: str = Query(default="1m", pattern="^(1m|5m|15m|30m|1h|1d)$"),
    limit: int = Query(default=300, ge=20, le=1200),
    db: AsyncSession = Depends(get_db),
) -> dict:
    candles, source = await load_candles(db, symbol, interval, limit)
    return {
        "schema_version": 1,
        "symbol": symbol,
        "interval": interval,
        "source": source,
        "count": len(candles),
        "candles": candles,
    }


@router.get("/history")
async def market_history(
    symbol: str = "R_100",
    interval: str = Query(default="1m", pattern="^(1m|5m|15m|30m|1h|1d)$"),
    before: int = Query(..., ge=1),
    limit: int = Query(default=1000, ge=50, le=1000),
) -> dict:
    """Older official candles ending before `before`. Uses its own socket, not the worker."""
    client = None
    try:
        from app.deriv.client import DerivWSClient

        client = DerivWSClient()
        await asyncio.wait_for(client.connect(), timeout=15)
        rows = await asyncio.wait_for(
            client.get_candles(
                symbol,
                granularity=INTERVAL_SECONDS[interval],
                count=limit,
                end=int(before) - 1,
            ),
            timeout=20,
        )
    except Exception as exc:
        return {
            "schema_version": 1,
            "symbol": symbol,
            "interval": interval,
            "before": before,
            "count": 0,
            "candles": [],
            "exhausted": False,
            "error": str(exc)[:200],
        }
    finally:
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass
    candles, wrapped = select_older_candles(rows, before)
    return {
        "schema_version": 1,
        "symbol": symbol,
        "interval": interval,
        "before": before,
        "count": len(candles),
        "candles": candles,
        "exhausted": wrapped or len(candles) < limit,
    }


@router.get("/ticks")
async def market_ticks(
    symbol: str = "R_100",
    limit: int = Query(default=400, ge=20, le=2000),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(
        select(Tick.epoch, Tick.quote)
        .where(Tick.symbol == symbol)
        .order_by(Tick.epoch.desc())
        .limit(limit)
    )
    points = [
        {"epoch": int(epoch), "quote": float(quote)}
        for epoch, quote in reversed(result.all())
    ]
    if points:
        market_hub.ingest_ticks(symbol, [(item["epoch"], item["quote"]) for item in points])
    return {"schema_version": 1, "symbol": symbol, "count": len(points), "points": points}


@router.post("/internal/ingest")
async def ingest_market(
    payload: dict,
    _: None = Depends(require_internal_secret),
) -> dict:
    symbol = str(payload.get("symbol") or "R_100")
    ticks = payload.get("ticks") or []
    if ticks:
        market_hub.ingest_ticks(
            symbol,
            [(int(item["epoch"]), float(item["quote"])) for item in ticks if "epoch" in item and "quote" in item],
        )
    candles = payload.get("candles") or {}
    if isinstance(candles, dict):
        for interval, rows in candles.items():
            if isinstance(rows, list):
                market_hub.ingest_candles(symbol, str(interval), rows, source="deriv_official")
    from app.notifications.browser import send_notification

    snapshot = market_hub.snapshot(symbol)
    await send_notification("market", snapshot, channels=("market",))
    return {"status": "ok", "snapshot": snapshot}


def ticks_frame(points: list[dict]) -> pd.DataFrame:
    if not points:
        return pd.DataFrame(columns=["epoch", "quote", "tick_time"])
    frame = pd.DataFrame(points)
    frame["tick_time"] = pd.to_datetime(frame["epoch"], unit="s", utc=True)
    return frame


def secret_matches(header: str | None) -> bool:
    return bool(settings.internal_api_secret) and header == settings.internal_api_secret


async def optional_secret(
    x_internal_secret: str | None = Header(default=None, alias="X-Internal-Secret"),
) -> None:
    if not secret_matches(x_internal_secret):
        raise HTTPException(status_code=403, detail="Forbidden")
    return None
