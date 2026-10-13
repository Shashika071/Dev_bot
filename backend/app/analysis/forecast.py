"""Read active ML heads without mixing them into the technical-analysis score."""

from __future__ import annotations

import json

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ml.direction_task import HORIZONS, predict_direction
from app.models.model_version import ModelVersion

_EMPTY = {
    "available": False,
    "up": None,
    "down": None,
    "neutral": None,
    "probability": None,
    "note": "No validated model is active.",
}


async def forecast_overlay(
    session: AsyncSession,
    symbol: str,
    ticks: pd.DataFrame | None = None,
) -> dict:
    heads = {task: dict(_EMPTY) for task in (*HORIZONS, "touch_upper", "touch_lower")}
    try:
        result = await session.execute(
            select(ModelVersion)
            .where(ModelVersion.symbol == symbol)
            .where(ModelVersion.is_active.is_(True))
        )
        rows = result.scalars().all()
    except Exception as exc:
        return {
            "separate_from_ta": True,
            "heads": heads,
            "note": f"Forecast store unavailable: {exc}",
        }
    for row in rows:
        task = str(row.task_id or "")
        try:
            metadata = json.loads(row.metadata_json or "{}")
        except json.JSONDecodeError:
            metadata = {}
        if task in HORIZONS and ticks is not None and not ticks.empty:
            predicted = predict_direction(ticks, metadata)
            if predicted:
                heads[task] = predicted
                continue
        if task in {"touch_upper", "touch_lower"} or str(row.direction) in {"upper", "lower"}:
            key = task if task in heads else f"touch_{row.direction}"
            if key in heads and row.metadata_json:
                heads[key] = {
                    "available": True,
                    "up": None,
                    "down": None,
                    "neutral": None,
                    "probability": None,
                    "note": "Active touch model is loaded separately from the chart score. Live probability is published with signal checks.",
                    "version_tag": row.version_tag,
                }
    return {
        "separate_from_ta": True,
        "heads": heads,
        "note": "Technical-analysis score and model probabilities are different measurements.",
    }
