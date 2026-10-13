"""Analysis snapshot, signal events, and user drawings."""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.engine import analyze_candles
from app.analysis.forecast import forecast_overlay
from app.api.market import load_candles, market_ticks
from app.database import get_db
from app.market_data.hub import market_hub
from app.models.market import ChartAnnotation
from app.schemas import AnalysisSnapshotResponse, AnnotationBody, AnnotationResponse
from app.signal_engine.chart_candles import INTERVAL_SECONDS

router = APIRouter(prefix="/analysis", tags=["analysis"])


def _annotation_dict(row: ChartAnnotation) -> dict:
    return {
        "id": row.id,
        "symbol": row.symbol,
        "interval": row.interval,
        "kind": row.kind,
        "geometry": json.loads(row.geometry_json or "{}"),
        "style": json.loads(row.style_json) if row.style_json else None,
        "label": row.label,
    }


@router.get("/snapshot", response_model=AnalysisSnapshotResponse)
async def analysis_snapshot(
    symbol: str = "R_100",
    interval: str = Query(default="1m", pattern="^(1m|5m|15m|30m|1h|1d)$"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    candles: dict[str, list] = {}
    fresh = True
    stale_reason = None
    for name in INTERVAL_SECONDS:
        rows, source = await load_candles(db, symbol, name, 500 if name == interval else 180)
        candles[name] = rows
        if name == interval and len(rows) < 30:
            fresh = False
            stale_reason = f"need more {interval} candles ({source})"
    snap = market_hub.snapshot(symbol)
    age = snap.get("tick_age_seconds")
    if age is None or float(age) > 30:
        fresh = False
        stale_reason = stale_reason or "tick data is stale"
    result = analyze_candles(
        candles,
        symbol=symbol,
        interval=interval,
        fresh=fresh,
        stale_reason=stale_reason,
    )
    tick_payload = await market_ticks(symbol=symbol, limit=400, db=db)
    from app.api.market import ticks_frame

    result["ml"] = await forecast_overlay(db, symbol, ticks_frame(tick_payload.get("points") or []))
    return result


@router.get("/annotations", response_model=list[AnnotationResponse])
async def list_annotations(
    symbol: str = "R_100",
    interval: str = "1m",
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    result = await db.execute(
        select(ChartAnnotation)
        .where(ChartAnnotation.symbol == symbol, ChartAnnotation.interval == interval)
        .order_by(ChartAnnotation.created_at.asc())
    )
    return [_annotation_dict(row) for row in result.scalars().all()]


@router.post("/annotations", response_model=AnnotationResponse)
async def create_annotation(body: AnnotationBody, db: AsyncSession = Depends(get_db)) -> dict:
    row = ChartAnnotation(
        id=str(uuid.uuid4()),
        symbol=body.symbol,
        interval=body.interval,
        kind=body.kind,
        geometry_json=json.dumps(body.geometry),
        style_json=json.dumps(body.style) if body.style else None,
        label=body.label,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _annotation_dict(row)


@router.delete("/annotations/{annotation_id}")
async def delete_annotation(annotation_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    result = await db.execute(delete(ChartAnnotation).where(ChartAnnotation.id == annotation_id))
    await db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Annotation not found")
    return {"ok": True, "deleted": annotation_id}
