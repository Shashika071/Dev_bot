"""Simple market regime labels from feature rows (vol / trend / range)."""

from __future__ import annotations

import numpy as np
import pandas as pd


def attach_regime_labels(
    features_df: pd.DataFrame,
    *,
    vol_low: float | None = None,
    vol_high: float | None = None,
    range_median: float | None = None,
) -> pd.DataFrame:
    """
    Add regime columns for gating and reporting.

    Volatility and range codes use boundaries fit on the training split.
    Without those boundaries the codes stay missing instead of being
    recomputed from whichever window happens to be in view.
    Trend uses the current row's crossover and slope only.
    """
    df = features_df.copy()
    vol = df.get("realized_vol_300")
    if vol is not None and vol_low is not None and vol_high is not None:
        vol_num = pd.to_numeric(vol, errors="coerce")
        labels = np.full(len(df), "unknown", dtype=object)
        valid = vol_num.notna().to_numpy()
        values = vol_num.to_numpy(dtype=float)
        labels[valid & (values <= float(vol_low))] = "low"
        labels[valid & (values > float(vol_low)) & (values < float(vol_high))] = "mid"
        labels[valid & (values >= float(vol_high))] = "high"
        df["vol_regime"] = labels
    else:
        df["vol_regime"] = "unknown"

    crossover = df.get("ma_crossover", pd.Series(0, index=df.index)).fillna(0)
    slope = df.get("ma_fast_slope", pd.Series(0, index=df.index)).fillna(0)
    trend = np.where(
        (crossover > 0) & (slope > 0),
        "up",
        np.where((crossover < 0) & (slope < 0), "down", "flat"),
    )
    df["trend_regime"] = trend

    rng = df.get("price_range_300")
    if rng is not None and range_median is not None:
        rng_num = pd.to_numeric(rng, errors="coerce")
        df["range_regime"] = np.where(rng_num >= float(range_median), "wide", "narrow")
    else:
        df["range_regime"] = "unknown"

    # Numeric encodings usable as model features. Unknown stays missing.
    vol_map = {"low": 0.0, "mid": 1.0, "high": 2.0}
    trend_map = {"down": -1.0, "flat": 0.0, "up": 1.0}
    range_map = {"narrow": 0.0, "wide": 1.0}
    df["vol_regime_code"] = df["vol_regime"].map(vol_map).astype(float)
    df["trend_regime_code"] = df["trend_regime"].map(trend_map).astype(float)
    df["range_regime_code"] = df["range_regime"].map(range_map).astype(float)
    return df


def regime_feature_columns() -> list[str]:
    return ["vol_regime_code", "trend_regime_code", "range_regime_code"]
