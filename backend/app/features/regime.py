"""Simple market regime labels from feature rows (vol / trend / range)."""

from __future__ import annotations

import numpy as np
import pandas as pd


def attach_regime_labels(features_df: pd.DataFrame) -> pd.DataFrame:
    """
    Add regime columns for gating and reporting.

    - vol_regime: low / mid / high from realized_vol_300 tertiles (in-sample ranks)
    - trend_regime: up / down / flat from ma_crossover + ma_fast_slope
    - range_regime: narrow / wide from price_range_300 vs median
    """
    df = features_df.copy()
    vol = df.get("realized_vol_300")
    if vol is not None and vol.notna().sum() >= 9:
        try:
            df["vol_regime"] = pd.qcut(
                vol.rank(method="first"), q=3, labels=["low", "mid", "high"]
            ).astype(str)
        except ValueError:
            df["vol_regime"] = "mid"
    else:
        df["vol_regime"] = "mid"

    crossover = df.get("ma_crossover", pd.Series(0, index=df.index)).fillna(0)
    slope = df.get("ma_fast_slope", pd.Series(0, index=df.index)).fillna(0)
    trend = np.where(
        (crossover > 0) & (slope > 0),
        "up",
        np.where((crossover < 0) & (slope < 0), "down", "flat"),
    )
    df["trend_regime"] = trend

    rng = df.get("price_range_300")
    if rng is not None and rng.notna().any():
        med = float(rng.median())
        df["range_regime"] = np.where(rng >= med, "wide", "narrow")
    else:
        df["range_regime"] = "narrow"

    # Numeric encodings usable as model features
    vol_map = {"low": 0.0, "mid": 1.0, "high": 2.0}
    trend_map = {"down": -1.0, "flat": 0.0, "up": 1.0}
    range_map = {"narrow": 0.0, "wide": 1.0}
    df["vol_regime_code"] = df["vol_regime"].map(vol_map).astype(float)
    df["trend_regime_code"] = df["trend_regime"].map(trend_map).astype(float)
    df["range_regime_code"] = df["range_regime"].map(range_map).astype(float)
    return df


def regime_feature_columns() -> list[str]:
    return ["vol_regime_code", "trend_regime_code", "range_regime_code"]
