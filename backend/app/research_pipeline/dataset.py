"""Build the learning table from ticks. Labels stay attached for evaluation only."""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.research_pipeline.features import (
    INDICATORS,
    RETURNS_VOL,
    STRATEGY,
    TOUCH_CONTEXT,
    build_tables,
    columns_for,
    lookup_features,
)
from app.research_pipeline.labels import decision_times, label_entries
from app.research_pipeline.settings import ExperimentConfig


def assemble_frame(clean: pd.DataFrame, cfg: ExperimentConfig) -> dict:
    epochs = clean["epoch"].to_numpy(dtype=np.int64)
    quotes = clean["quote"].to_numpy(dtype=float)
    tables = build_tables(epochs, quotes, cfg)
    end = int(epochs[-1]) - int(cfg.horizon_seconds)
    times = decision_times(epochs, cfg, end_epoch=end)
    labels = label_entries(epochs, quotes, times, cfg)
    rows = []
    for stamp in times:
        features = lookup_features(tables, int(stamp), cfg)
        if features is not None:
            rows.append(features)
    features = pd.DataFrame(rows)
    if features.empty:
        merged = features
    else:
        merged = features.merge(labels, left_on="asof", right_on="entry_epoch", how="inner")
    required = columns_for("strategy", "upper_touch")
    finite = 0
    if not merged.empty:
        finite = int(np.isfinite(merged.loc[:, required].to_numpy(dtype=float)).all(axis=1).sum())
    return {
        "tables": tables,
        "labels": labels,
        "frame": merged,
        "decision_count": int(len(times)),
        "feature_row_count": int(len(features)),
        "finite_feature_rows": finite,
        "label_counts": labels["status"].value_counts().astype(int).to_dict() if len(labels) else {},
        "tie_count": int(labels["tie"].sum()) if len(labels) else 0,
        "feature_columns": {
            "returns_vol": list(RETURNS_VOL),
            "indicators": list(INDICATORS),
            "strategy": list(STRATEGY),
            "touch_context": list(TOUCH_CONTEXT),
        },
    }
