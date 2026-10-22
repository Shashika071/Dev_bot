"""Causal R_100 features. Row t uses only candles that are closed at t."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.synthetic.indicators import sma, technical_series, wilder
from app.trading_ml.data.resample import resample_candles

FEATURE_COLUMNS = [
    "ret_1", "ret_5", "ret_15",
    "body", "upper_wick", "lower_wick",
    "dist_high", "dist_low",
    "breakout_up", "breakout_down",
    "pullback",
    "rsi14", "macd_hist", "adx14", "atr_pct",
    "bb_pct", "ema_gap", "sma_gap", "stoch_k",
    "rv_10", "rv_20", "rv_60", "rv_ratio", "range_pct",
    "m5_trend", "m15_trend", "h1_trend", "d1_trend",
    "mtf_agreement", "mtf_available",
]
TREND_COLUMNS = ["m5_trend", "m15_trend", "h1_trend", "d1_trend"]


def _adx(candles: list[dict], period: int = 14) -> list[float]:
    trs, plus, minus = [], [], []
    previous_close = float(candles[0]["close"]) if candles else 0.0
    previous_high = float(candles[0]["high"]) if candles else 0.0
    previous_low = float(candles[0]["low"]) if candles else 0.0
    for index, row in enumerate(candles):
        high, low, close = float(row["high"]), float(row["low"]), float(row["close"])
        up = high - previous_high if index else 0.0
        down = previous_low - low if index else 0.0
        plus.append(up if up > down and up > 0 else 0.0)
        minus.append(down if down > up and down > 0 else 0.0)
        trs.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
        previous_close, previous_high, previous_low = close, high, low
    atr_values = wilder(trs, period)
    plus_dm = wilder(plus, period)
    minus_dm = wilder(minus, period)
    dx = []
    for atr_value, plus_value, minus_value in zip(atr_values, plus_dm, minus_dm):
        if atr_value <= 0:
            dx.append(0.0)
            continue
        plus_di = 100.0 * plus_value / atr_value
        minus_di = 100.0 * minus_value / atr_value
        total = plus_di + minus_di
        dx.append(0.0 if total == 0 else 100.0 * abs(plus_di - minus_di) / total)
    return wilder(dx, period)


def feature_frame(candles: list[dict], step: int = 60) -> pd.DataFrame:
    """One row per closed candle. Rolling windows are trailing, including that close."""
    frame = pd.DataFrame(candles)
    if frame.empty:
        return pd.DataFrame(columns=["epoch", "decision_epoch", *FEATURE_COLUMNS])
    for column in ("open", "high", "low", "close"):
        frame[column] = frame[column].astype(float)
    frame["epoch"] = frame["epoch"].astype("int64")
    frame = frame.sort_values("epoch").drop_duplicates("epoch", keep="last").reset_index(drop=True)
    technical = technical_series(candles_from_frame(frame))
    close = frame["close"]
    span = (frame["high"] - frame["low"]).replace(0, np.nan)
    frame["ret_1"] = close.pct_change(1)
    frame["ret_5"] = close.pct_change(5)
    frame["ret_15"] = close.pct_change(15)
    frame["body"] = (close - frame["open"]) / span
    frame["upper_wick"] = (frame["high"] - np.maximum(frame["open"], close)) / span
    frame["lower_wick"] = (np.minimum(frame["open"], close) - frame["low"]) / span
    prior_high = frame["high"].shift(1).rolling(20).max()
    prior_low = frame["low"].shift(1).rolling(20).min()
    atr = pd.Series(technical["atr"], index=frame.index).replace(0, np.nan)
    frame["dist_high"] = (prior_high - close) / atr
    frame["dist_low"] = (close - prior_low) / atr
    frame["breakout_up"] = (close > prior_high).astype(float)
    frame["breakout_down"] = (close < prior_low).astype(float)
    frame["rsi14"] = technical["rsi"]
    frame["macd_hist"] = pd.Series(technical["macd_hist"], index=frame.index) / close
    frame["adx14"] = _adx(candles_from_frame(frame))
    frame["atr_pct"] = atr / close
    midpoint = close.rolling(20).mean()
    width = close.rolling(20).std(ddof=0)
    frame["bb_pct"] = (close - (midpoint - 2 * width)) / (4 * width.replace(0, np.nan))
    ema20 = pd.Series(technical["ema20"], index=frame.index, dtype=float)
    sma20 = pd.to_numeric(pd.Series(sma([float(value) for value in close], 20)), errors="coerce")
    frame["ema_gap"] = (close - ema20) / atr
    frame["sma_gap"] = (close - sma20) / atr
    lowest = frame["low"].rolling(14).min()
    highest = frame["high"].rolling(14).max()
    frame["stoch_k"] = (close - lowest) / (highest - lowest).replace(0, np.nan)
    frame["rv_10"] = frame["ret_1"].rolling(10).std()
    frame["rv_20"] = frame["ret_1"].rolling(20).std()
    frame["rv_60"] = frame["ret_1"].rolling(60).std()
    frame["rv_ratio"] = frame["rv_10"] / frame["rv_60"].replace(0, np.nan)
    frame["range_pct"] = span / close
    pullback = np.zeros(len(frame))
    gap = frame["ema_gap"].to_numpy(dtype=float)
    ret = frame["ret_1"].to_numpy(dtype=float)
    pullback[(gap > 0) & (ret < 0)] = 1
    pullback[(gap < 0) & (ret > 0)] = -1
    frame["pullback"] = pullback
    frame["decision_epoch"] = frame["epoch"] + int(step)
    for column in TREND_COLUMNS:
        frame[column] = np.nan
    _attach_trends(frame, candles_from_frame(frame), step)
    return frame


def candles_from_frame(frame: pd.DataFrame) -> list[dict]:
    return frame[["epoch", "open", "high", "low", "close"]].to_dict("records")


def _trend_frame(candles: list[dict], step: int, name: str) -> pd.DataFrame:
    if len(candles) < 4:
        return pd.DataFrame(columns=["available_at", name])
    closes = [float(row["close"]) for row in candles]
    trend = [0.0] * len(closes)
    for index in range(3, len(closes)):
        change = closes[index] - closes[index - 3]
        trend[index] = float(np.sign(change)) if change else 0.0
    return pd.DataFrame({
        "available_at": [int(row["epoch"]) + step for row in candles],
        name: trend,
    })


def _attach_trends(frame: pd.DataFrame, candles: list[dict], step: int) -> None:
    mapping = {"m5_trend": 300, "m15_trend": 900, "h1_trend": 3600, "d1_trend": 86400}
    left = frame.sort_values("decision_epoch")
    for name, target in mapping.items():
        higher = resample_candles(candles, step, target)
        trend = _trend_frame(higher, target, name)
        if trend.empty:
            continue
        trend = trend.sort_values("available_at").drop_duplicates("available_at", keep="last")
        merged = pd.merge_asof(
            left[["decision_epoch"]], trend, left_on="decision_epoch",
            right_on="available_at", direction="backward",
        )
        frame[name] = merged[name].to_numpy()
    available = frame[TREND_COLUMNS]
    frame["mtf_available"] = available.notna().sum(axis=1).astype(float)
    frame["mtf_agreement"] = available.mean(axis=1, skipna=True).fillna(0.0)
    frame[TREND_COLUMNS] = available.fillna(0.0)


def align_entry(decision_epoch: int, delay_seconds: int, step: int) -> int:
    """Move the entry to the next candle open that is not earlier than the delay."""
    entry_time = int(decision_epoch) + int(delay_seconds)
    if entry_time % step == 0:
        return entry_time
    return (entry_time // step + 1) * step


def contract_labels(candles: list[dict], *, duration: int, entry_delay: int = 0,
                    step: int = 60) -> pd.DataFrame:
    """Label a candle only when every settlement candle exists."""
    by_epoch = {int(row["epoch"]): row for row in candles}
    rows = []
    for epoch in sorted(by_epoch):
        decision = epoch + step
        entry = align_entry(decision, entry_delay, step)
        exit_epoch = entry + duration - step
        needed = list(range(entry, exit_epoch + step, step))
        if any(item not in by_epoch for item in needed):
            continue
        entry_price = float(by_epoch[entry]["open"])
        exit_price = float(by_epoch[exit_epoch]["close"])
        tie = exit_price == entry_price
        label = None if tie else int(exit_price > entry_price)
        rows.append({
            "epoch": epoch,
            "decision_epoch": decision,
            "entry_epoch": entry,
            "expiry_epoch": entry + duration,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "label": label,
            "tie": tie,
        })
    return pd.DataFrame(rows)
