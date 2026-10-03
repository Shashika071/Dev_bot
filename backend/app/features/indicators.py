"""
Technical indicators for feature engineering.

All indicators operate on price Series data.
Period parameters are configurable research candidates — not assumed optimal.
"""

import numpy as np
import pandas as pd
from typing import Optional


def moving_average(prices: pd.Series, period: int) -> pd.Series:
    """Simple Moving Average."""
    return prices.rolling(window=period, min_periods=period).mean()


def exponential_moving_average(prices: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average."""
    return prices.ewm(span=period, min_periods=period, adjust=False).mean()


def ma_slope(ma_series: pd.Series, lookback: int = 5) -> pd.Series:
    """Slope of a moving average over lookback periods."""
    return ma_series.diff(lookback) / lookback


def ma_crossover_state(fast_ma: pd.Series, slow_ma: pd.Series) -> pd.Series:
    """
    Returns crossover state:
    +1 = fast above slow, -1 = fast below slow, 0 = crossing.
    """
    diff = fast_ma - slow_ma
    return np.sign(diff)


def rsi(prices: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index."""
    delta = prices.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)

    avg_gain = gain.rolling(window=period, min_periods=period).mean()
    avg_loss = loss.rolling(window=period, min_periods=period).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def bollinger_bands(
    prices: pd.Series,
    period: int = 20,
    num_std: float = 2.0,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    Returns (upper_band, middle_band, lower_band).
    Settings are hypotheses to test, not assumed optimal.
    """
    middle = prices.rolling(window=period, min_periods=period).mean()
    std = prices.rolling(window=period, min_periods=period).std()
    upper = middle + (std * num_std)
    lower = middle - (std * num_std)
    return upper, middle, lower


def bollinger_position(
    prices: pd.Series,
    upper: pd.Series,
    lower: pd.Series,
) -> pd.Series:
    """Position within Bollinger Bands: 0 = at lower, 1 = at upper."""
    band_width = upper - lower
    return (prices - lower) / band_width.replace(0, np.nan)


def bollinger_bandwidth(upper: pd.Series, lower: pd.Series, middle: pd.Series) -> pd.Series:
    """Bollinger Band width normalized by middle band."""
    return (upper - lower) / middle.replace(0, np.nan)


def donchian_channel(
    highs: pd.Series,
    lows: pd.Series,
    period: int = 20,
) -> tuple[pd.Series, pd.Series]:
    """
    Donchian Channel (high/low of prior completed periods).
    IMPORTANT: Uses shift(1) to exclude the current period from the channel.
    """
    upper = highs.rolling(window=period, min_periods=period).max().shift(1)
    lower = lows.rolling(window=period, min_periods=period).min().shift(1)
    return upper, lower


def donchian_breakout_state(
    prices: pd.Series,
    upper_channel: pd.Series,
    lower_channel: pd.Series,
) -> pd.Series:
    """
    Breakout state relative to Donchian channel:
    +1 = above upper channel, -1 = below lower channel, 0 = inside.
    """
    state = pd.Series(0, index=prices.index)
    state[prices > upper_channel] = 1
    state[prices < lower_channel] = -1
    return state


def realised_volatility(prices: pd.Series, window: int = 60) -> pd.Series:
    """Realised volatility (standard deviation of returns)."""
    returns = prices.pct_change()
    return returns.rolling(window=window, min_periods=window).std()


def price_range(
    highs: pd.Series,
    lows: pd.Series,
    window: int = 60,
) -> pd.Series:
    """Rolling price range (high - low)."""
    rolling_high = highs.rolling(window=window, min_periods=window).max()
    rolling_low = lows.rolling(window=window, min_periods=window).min()
    return rolling_high - rolling_low


def returns_over_window(prices: pd.Series, windows: list[int]) -> dict[str, pd.Series]:
    """Returns over multiple lookback windows."""
    result = {}
    for w in windows:
        result[f"return_{w}"] = prices.pct_change(periods=w)
    return result


def distance_to_level(prices: pd.Series, level: pd.Series) -> pd.Series:
    """Distance from current price to a reference level (absolute)."""
    return prices - level


def distance_to_level_pct(prices: pd.Series, level: pd.Series) -> pd.Series:
    """Distance from current price to a reference level (percentage)."""
    return (prices - level) / prices.replace(0, np.nan) * 100


def tick_cadence(epochs: pd.Series, window: int = 60) -> pd.Series:
    """Rolling count of ticks in the window (tick cadence/frequency)."""
    diffs = epochs.diff()
    # Count ticks where diff > 0 (i.e., actual new ticks)
    return (diffs > 0).rolling(window=window, min_periods=1).sum()
