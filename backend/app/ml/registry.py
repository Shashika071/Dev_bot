"""Persist and activate trained model versions in the database."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.model_version import ModelVersion

logger = structlog.get_logger(__name__)


def _parse_ts(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


async def save_and_activate_model_version(
    session: AsyncSession,
    metadata: dict,
    results: Optional[dict] = None,
) -> ModelVersion:
    """
    Insert a ModelVersion row from training metadata and mark it active
    for its (symbol, direction). Deactivates prior active rows for that pair.
    """
    results = results or {}
    direction = metadata.get("direction") or "upper"
    symbol = metadata.get("symbol") or "R_100"
    version_tag = metadata.get("version_tag") or f"v-{direction}-unknown"
    algorithm = metadata.get("selected_pipeline") or "catboost"
    metrics = metadata.get("metrics") or {}
    split = metadata.get("split_info") or {}
    paths = metadata.get("paths") or {}

    # Deactivate previous active models for this symbol+direction
    await session.execute(
        update(ModelVersion)
        .where(ModelVersion.symbol == symbol)
        .where(ModelVersion.direction == direction)
        .where(ModelVersion.is_active.is_(True))
        .values(is_active=False)
    )

    existing = await session.execute(
        select(ModelVersion).where(ModelVersion.version_tag == version_tag)
    )
    row = existing.scalar_one_or_none()
    if row is None:
        row = ModelVersion(version_tag=version_tag)
        session.add(row)

    row.model_name = f"{algorithm}_{direction}_touch"
    row.algorithm = str(algorithm)
    row.direction = direction
    row.symbol = symbol
    row.barrier_distance = float(metadata.get("barrier_distance") or 0)
    row.duration_seconds = int(metadata.get("duration_seconds") or 540)
    row.n_train_samples = split.get("train")
    row.n_val_samples = split.get("val")
    row.n_cal_samples = split.get("cal") or metadata.get("calibration_n_samples")
    row.n_test_samples = split.get("test")
    row.sampling_interval_seconds = metadata.get("sampling_interval_seconds")
    row.effective_sample_count = metadata.get("effective_sample_count")
    row.purge_gap_seconds = metadata.get("gap_seconds")
    row.walk_forward_folds = metadata.get("walk_forward_folds")
    row.feature_names = json.dumps(metadata.get("feature_columns") or [])
    row.n_features = len(metadata.get("feature_columns") or [])
    row.brier_score = metrics.get("brier_score")
    row.log_loss = metrics.get("log_loss")
    row.auc_roc = metrics.get("auc_roc")
    row.selected_signal_win_rate = metrics.get("selected_win_rate")
    row.selected_signal_count = metrics.get("selected_signal_count")
    row.has_demonstrated_edge = bool(metadata.get("has_demonstrated_edge", False))
    row.edge_description = metadata.get("edge_description")
    row.is_active = True
    row.alerts_paused = False
    row.pause_reason = None
    row.model_path = metadata.get("model_path") or paths.get(algorithm)
    row.calibrator_path = metadata.get("calibrator_path") or paths.get("calibrator")
    row.metadata_json = json.dumps(metadata, default=str)
    row.notes = (results.get("quote_backtest_note") or metadata.get("quote_backtest_note"))
    row.created_at = _parse_ts(metadata.get("created_at")) or datetime.now(timezone.utc)

    await session.commit()
    await session.refresh(row)
    logger.info(
        "model_version_activated",
        id=row.id,
        version_tag=version_tag,
        direction=direction,
        has_edge=row.has_demonstrated_edge,
    )
    return row


async def get_active_model_version(
    session: AsyncSession,
    *,
    symbol: str,
    direction: str,
) -> Optional[ModelVersion]:
    result = await session.execute(
        select(ModelVersion)
        .where(ModelVersion.symbol == symbol)
        .where(ModelVersion.direction == direction)
        .where(ModelVersion.is_active.is_(True))
        .order_by(ModelVersion.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def set_model_alerts_paused(
    session: AsyncSession,
    *,
    version_id: int,
    paused: bool,
    reason: Optional[str] = None,
) -> None:
    await session.execute(
        update(ModelVersion)
        .where(ModelVersion.id == version_id)
        .values(alerts_paused=paused, pause_reason=reason)
    )
    await session.commit()
