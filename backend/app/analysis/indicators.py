"""OHLC indicators for chart overlays and strategy features.

R_100 does not publish exchange volume, so volume-based indicators are omitted.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.features.indicators import (
    bollinger_bands,
    donchian_channel,
    exponential_moving_average,
    moving_average,
    rsi,
)


def frame(candles: list[dict]) -> pd.DataFrame:
    if not candles:
        return pd.DataFrame(columns=["epoch", "open", "high", "low", "close"])
    df = pd.DataFrame(candles).copy()
    for column in ("epoch", "open", "high", "low", "close"):
        df[column] = pd.to_numeric(df[column], errors="coerce")
    return df.dropna(subset=["epoch", "open", "high", "low", "close"]).reset_index(drop=True)


def _series(values: pd.Series) -> list[dict]:
    out = []
    for epoch, value in values.items():
        if pd.isna(value):
            continue
        out.append({"epoch": int(epoch), "value": float(value)})
    return out


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev = df["close"].shift(1)
    true_range = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev).abs(),
            (df["low"] - prev).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.rolling(period, min_periods=period).mean()


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    macd_line = exponential_moving_average(close, fast) - exponential_moving_average(close, slow)
    signal_line = exponential_moving_average(macd_line.dropna(), signal).reindex(macd_line.index)
    hist = macd_line - signal_line
    return pd.DataFrame({"macd": macd_line, "signal": signal_line, "hist": hist})


def stochastic(df: pd.DataFrame, period: int = 14, smooth: int = 3) -> pd.DataFrame:
    lowest = df["low"].rolling(period, min_periods=period).min()
    highest = df["high"].rolling(period, min_periods=period).max()
    span = (highest - lowest).replace(0, np.nan)
    k = (df["close"] - lowest) / span * 100
    d = k.rolling(smooth, min_periods=smooth).mean()
    return pd.DataFrame({"k": k, "d": d})


def adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    prev = df["close"].shift(1)
    true_range = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()],
        axis=1,
    ).max(axis=1)
    atr_sum = true_range.rolling(period, min_periods=period).sum()
    plus_di = 100 * plus_dm.rolling(period, min_periods=period).sum() / atr_sum.replace(0, np.nan)
    minus_di = 100 * minus_dm.rolling(period, min_periods=period).sum() / atr_sum.replace(0, np.nan)
    dx = (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan) * 100
    return pd.DataFrame({
        "adx": dx.rolling(period, min_periods=period).mean(),
        "plus_di": plus_di,
        "minus_di": minus_di,
    })


def indicator_snapshot(candles: list[dict]) -> dict:
    df = frame(candles)
    if len(df) < 30:
        return {"ready": False, "last": {}, "series": {}}
    df = df.set_index("epoch", drop=False)
    close = df["close"]
    ema8 = exponential_moving_average(close, 8)
    ema21 = exponential_moving_average(close, 21)
    ema20 = exponential_moving_average(close, 20)
    ema50 = exponential_moving_average(close, 50)
    sma20 = moving_average(close, 20)
    rsi14 = rsi(close, 14)
    bands = bollinger_bands(close, 20, 2.0)
    upper_dc, lower_dc = donchian_channel(df["high"], df["low"], 20)
    macd_df = macd(close)
    stoch = stochastic(df)
    adx_df = adx(df)
    atr14 = atr(df, 14)
    last = df.iloc[-1]
    def last_value(series: pd.Series) -> float | None:
        value = series.iloc[-1]
        if pd.isna(value):
            return None
        return float(value)

    return {
        "ready": True,
        "last": {
            "close": float(last["close"]),
            "ema8": last_value(ema8),
            "ema21": last_value(ema21),
            "ema20": last_value(ema20),
            "ema50": last_value(ema50),
            "sma20": last_value(sma20),
            "rsi": last_value(rsi14),
            "macd": last_value(macd_df["macd"]),
            "macd_signal": last_value(macd_df["signal"]),
            "macd_hist": last_value(macd_df["hist"]),
            "bb_upper": last_value(bands[0]),
            "bb_middle": last_value(bands[1]),
            "bb_lower": last_value(bands[2]),
            "atr": last_value(atr14),
            "adx": last_value(adx_df["adx"]),
            "plus_di": last_value(adx_df["plus_di"]),
            "minus_di": last_value(adx_df["minus_di"]),
            "stoch_k": last_value(stoch["k"]),
            "stoch_d": last_value(stoch["d"]),
            "donchian_upper": last_value(upper_dc),
            "donchian_lower": last_value(lower_dc),
        },
        "series": {
            "ema8": _series(ema8),
            "ema21": _series(ema21),
            "ema20": _series(ema20),
            "ema50": _series(ema50),
            "bb_upper": _series(bands[0]),
            "bb_lower": _series(bands[2]),
            "donchian_upper": _series(upper_dc),
            "donchian_lower": _series(lower_dc),
        },
    }


def last_atr(candles: list[dict], period: int = 14) -> float:
    df = frame(candles)
    if len(df) < period + 1:
        if df.empty:
            return 0.0
        return float((df["high"] - df["low"]).median())
    value = atr(df, period).iloc[-1]
    if pd.isna(value):
        return float((df["high"] - df["low"]).median())
    return float(value)
