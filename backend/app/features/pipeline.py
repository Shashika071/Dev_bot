"""
Feature engineering pipeline — computes all features from tick data
for ML model training and inference.

All features use ONLY information available at the decision time.
Scalers and feature selection are fit on training data only.
"""

import numpy as np
import pandas as pd
from typing import Optional
import structlog

from app.features.indicators import (
    moving_average,
    exponential_moving_average,
    ma_slope,
    ma_crossover_state,
    rsi,
    bollinger_bands,
    bollinger_position,
    bollinger_bandwidth,
    donchian_channel,
    donchian_breakout_state,
    realised_volatility,
    returns_over_window,
    tick_cadence,
)

logger = structlog.get_logger(__name__)

# Feature groups for documentation and ablation studies
FEATURE_GROUPS = {
    "barrier": [
        "barrier_distance_points",
        "barrier_distance_pct",
        "barrier_distance_vol_normalized",
    ],
    "returns": [
        "return_10", "return_30", "return_60",
        "return_120", "return_300", "return_540",
    ],
    "volatility": [
        "realized_vol_60", "realized_vol_120",
        "realized_vol_300", "realized_vol_540",
        "price_range_60", "price_range_300",
    ],
    "trend": [
        "ma_fast_dist", "ma_slow_dist",
        "ma_fast_slope", "ma_slow_slope",
        "ma_crossover",
    ],
    "oscillator": [
        "rsi_14", "rsi_28",
        "bb_position", "bb_bandwidth",
    ],
    "breakout": [
        "donchian_breakout_state",
        "dist_to_donchian_upper",
        "dist_to_donchian_lower",
    ],
    "microstructure": [
        "tick_cadence_60",
        "missing_data_flag",
        "seconds_since_last_tick",
    ],
}

ALL_FEATURES = [f for group in FEATURE_GROUPS.values() for f in group]


def build_features(
    ticks_df: pd.DataFrame,
    barrier_distance: float,
    barrier_direction: str = "upper",
    ma_fast_period: int = 6,
    ma_slow_period: int = 21,
    rsi_period: int = 14,
    bb_period: int = 20,
    bb_std: float = 2.0,
    donchian_period: int = 20,
) -> pd.DataFrame:
    """
    Build feature matrix from tick data.

    All indicators use configurable periods — these are research candidates,
    not assumed optimal values.

    Args:
        ticks_df: DataFrame with columns ['epoch', 'tick_time', 'quote'].
        barrier_distance: Barrier offset for distance features.
        barrier_direction: "upper" or "lower".
        ma_fast_period: Fast MA period (candidate, not optimal).
        ma_slow_period: Slow MA period (candidate, not optimal).
        rsi_period: RSI period (candidate, not optimal).
        bb_period: Bollinger period (candidate, not optimal).
        bb_std: Bollinger std multiplier (candidate, not optimal).
        donchian_period: Donchian channel period (candidate, not optimal).

    Returns:
        DataFrame with one row per tick, containing all computed features.
    """
    df = ticks_df.copy()
    prices = df["quote"]

    # --- Barrier Distance ---
    df["barrier_distance_points"] = barrier_distance
    df["barrier_distance_pct"] = barrier_distance / prices.replace(0, np.nan) * 100

    # --- Returns ---
    return_windows = [10, 30, 60, 120, 300, 540]
    returns = returns_over_window(prices, return_windows)
    for name, series in returns.items():
        df[name] = series

    # --- Volatility ---
    for window in [60, 120, 300, 540]:
        df[f"realized_vol_{window}"] = realised_volatility(prices, window)

    # Rolling range using rolling max/min of prices
    for window in [60, 300]:
        rolling_high = prices.rolling(window=window, min_periods=window).max()
        rolling_low = prices.rolling(window=window, min_periods=window).min()
        df[f"price_range_{window}"] = rolling_high - rolling_low

    # Barrier distance normalized by volatility
    vol_60 = df.get("realized_vol_60", pd.Series(np.nan, index=df.index))
    df["barrier_distance_vol_normalized"] = (
        barrier_distance / (vol_60 * prices).replace(0, np.nan)
    )

    # --- Moving Averages (Trend) ---
    fast_ma = exponential_moving_average(prices, ma_fast_period)
    slow_ma = exponential_moving_average(prices, ma_slow_period)

    df["ma_fast_dist"] = (prices - fast_ma) / prices.replace(0, np.nan) * 100
    df["ma_slow_dist"] = (prices - slow_ma) / prices.replace(0, np.nan) * 100
    df["ma_fast_slope"] = ma_slope(fast_ma, lookback=5)
    df["ma_slow_slope"] = ma_slope(slow_ma, lookback=5)
    df["ma_crossover"] = ma_crossover_state(fast_ma, slow_ma)

    # --- RSI ---
    df["rsi_14"] = rsi(prices, period=rsi_period)
    df["rsi_28"] = rsi(prices, period=rsi_period * 2)

    # --- Bollinger Bands ---
    bb_upper, bb_middle, bb_lower = bollinger_bands(prices, bb_period, bb_std)
    df["bb_position"] = bollinger_position(prices, bb_upper, bb_lower)
    df["bb_bandwidth"] = bollinger_bandwidth(bb_upper, bb_lower, bb_middle)

    # --- Donchian Channel ---
    # Uses shift(1) internally — excludes current period from channel
    rolling_high = prices.rolling(window=donchian_period, min_periods=donchian_period).max()
    rolling_low = prices.rolling(window=donchian_period, min_periods=donchian_period).min()
    dc_upper, dc_lower = donchian_channel(rolling_high, rolling_low, donchian_period)

    df["donchian_breakout_state"] = donchian_breakout_state(prices, dc_upper, dc_lower)
    df["dist_to_donchian_upper"] = (dc_upper - prices) / prices.replace(0, np.nan) * 100
    df["dist_to_donchian_lower"] = (prices - dc_lower) / prices.replace(0, np.nan) * 100

    # --- Microstructure ---
    df["tick_cadence_60"] = tick_cadence(df["epoch"], window=60)
    df["seconds_since_last_tick"] = df["epoch"].diff()
    df["missing_data_flag"] = (df["seconds_since_last_tick"] > 5).astype(int)

    return df


def get_feature_columns() -> list[str]:
    """Return list of all feature column names."""
    return ALL_FEATURES.copy()


def select_feature_matrix(features_df: pd.DataFrame) -> pd.DataFrame:
    """
    Select the canonical model feature columns in stable order.
    Missing columns are filled with NaN so train/infer stay aligned.
    """
    cols = get_feature_columns()
    out = features_df.copy()
    for c in cols:
        if c not in out.columns:
            out[c] = np.nan
    return out[cols]


def validate_no_future_leakage(
    feature_epochs: list[int],
    entry_epochs: list[int],
) -> bool:
    """
    Per-sample check: feature epoch must be <= entry epoch.
    Returns True if no leakage detected.
    """
    if not feature_epochs or not entry_epochs:
        return True
    if len(feature_epochs) != len(entry_epochs):
        logger.error(
            "FUTURE_LEAKAGE_LENGTH_MISMATCH",
            n_features=len(feature_epochs),
            n_entries=len(entry_epochs),
        )
        return False

    for fe, ee in zip(feature_epochs, entry_epochs):
        if int(fe) > int(ee):
            logger.error(
                "FUTURE_LEAKAGE_DETECTED",
                feature_epoch=int(fe),
                entry_epoch=int(ee),
            )
            return False
    return True
