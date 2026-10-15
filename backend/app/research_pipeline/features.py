"""Causal features from completed candles only.

A candle is usable at time T only when its close time is <= T.
Tick count is stored for data quality and is not a model feature.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.research_pipeline.candles import candle_tables
from app.research_pipeline.settings import ExperimentConfig

RETURNS_VOL = [
    "ret_1m",
    "ret_3m",
    "ret_5m",
    "ret_15m",
    "vol_5m",
    "vol_15m",
    "price_vol_5",
]
INDICATORS = [
    "ema_diff_8_21",
    "ema_slope_8",
    "ema_dist_8",
    "rsi_14",
    "roc_10",
    "atr_14_norm",
    "range_norm",
    "bb_position",
    "bb_width",
    "dist_prior_high_20",
    "dist_prior_low_20",
    "body_pct",
    "upper_wick_pct",
    "lower_wick_pct",
    "streak_up",
    "streak_down",
    "ret_15s",
    "range_15s_norm",
]
STRATEGY = [
    "trend_up",
    "trend_down",
    "mean_revert_up",
    "mean_revert_down",
    "breakout_up",
    "breakout_down",
    "vol_expansion",
    "vol_contraction",
]
TOUCH_CONTEXT = [
    "barrier_distance",
    "time_to_expiry_seconds",
    "barrier_over_vol",
]
SEQ_COLUMNS = ["ret_1m", "vol_5m", "rsi_14"]


def columns_for(group: str, target: str) -> list[str]:
    if group not in ("returns_vol", "indicators", "strategy"):
        raise ValueError(group)
    columns = list(RETURNS_VOL)
    if group in ("indicators", "strategy"):
        columns.extend(INDICATORS)
    if group == "strategy":
        columns.extend(STRATEGY)
    if target in ("upper_touch", "lower_touch"):
        columns.extend(TOUCH_CONTEXT)
    return columns


def build_tables(epochs: np.ndarray, quotes: np.ndarray, cfg: ExperimentConfig) -> dict:
    epochs = np.asarray(epochs, dtype=np.int64)
    quotes = np.asarray(quotes, dtype=float)
    order = np.argsort(epochs, kind="mergesort")
    epochs = epochs[order]
    quotes = quotes[order]
    all_1m, usable_1m = candle_tables(epochs, quotes, 60, cfg.max_gap_seconds)
    all_15, usable_15 = candle_tables(epochs, quotes, 15, cfg.max_gap_seconds)
    return {
        "epochs": epochs,
        "quotes": quotes,
        "all_1m": all_1m,
        "all_15s": all_15,
        "m1": _enrich_minute(usable_1m),
        "m15": _enrich_fast(usable_15),
        "gapped_1m": int(all_1m["gapped"].sum()) if len(all_1m) else 0,
        "gapped_15s": int(all_15["gapped"].sum()) if len(all_15) else 0,
        "completed_1m": int(len(usable_1m)),
        "completed_15s": int(len(usable_15)),
    }


def lookup_features(tables: dict, asof: int, cfg: ExperimentConfig) -> dict | None:
    minute = tables["m1"]
    fast = tables["m15"]
    if minute.empty or fast.empty:
        return None
    i = int(np.searchsorted(minute["close_time"].to_numpy(), int(asof), side="right") - 1)
    j = int(np.searchsorted(fast["close_time"].to_numpy(), int(asof), side="right") - 1)
    if i < 0 or j < 0:
        return None
    row = minute.iloc[i]
    fast_row = fast.iloc[j]
    if int(row["close_time"]) > int(asof) or int(fast_row["close_time"]) > int(asof):
        return None
    values = {name: float(row[name]) for name in RETURNS_VOL + INDICATORS if name not in ("ret_15s", "range_15s_norm")}
    values["ret_15s"] = float(fast_row["ret_15s"])
    values["range_15s_norm"] = float(fast_row["range_15s_norm"])
    for name in STRATEGY:
        values[name] = float(row[name])
    price_vol = float(row["price_vol_5"])
    values["barrier_distance"] = float(cfg.barrier_distance)
    values["time_to_expiry_seconds"] = float(cfg.horizon_seconds)
    values["barrier_over_vol"] = (
        float(cfg.barrier_distance / price_vol) if np.isfinite(price_vol) and price_vol > 0 else np.nan
    )
    values["asof"] = int(asof)
    values["feature_close_time"] = int(row["close_time"])
    return values


def future_mutation_changes_features(
    epochs: np.ndarray,
    quotes: np.ndarray,
    asof: int,
    cfg: ExperimentConfig,
) -> bool:
    """True when a tick after `asof` changes the feature vector at `asof`."""
    before = lookup_features(build_tables(epochs, quotes, cfg), asof, cfg)
    mutated = np.array(quotes, dtype=float, copy=True)
    mutated[np.asarray(epochs) > int(asof)] = mutated[np.asarray(epochs) > int(asof)] + 50.0
    after = lookup_features(build_tables(epochs, mutated, cfg), asof, cfg)
    if before is None or after is None:
        return before is not None or after is not None
    keys = [key for key in before if key not in ("asof", "feature_close_time")]
    for key in keys:
        if not np.isclose(before[key], after[key], equal_nan=True):
            return True
    return False


def sequence_matrix(tables: dict, asofs: np.ndarray, seq_len: int) -> tuple[np.ndarray, np.ndarray]:
    minute = tables["m1"]
    close_time = minute["close_time"].to_numpy()
    segment = minute["segment"].to_numpy()
    data = minute.loc[:, SEQ_COLUMNS].to_numpy(dtype=float)
    sequences = np.full((len(asofs), seq_len, len(SEQ_COLUMNS)), np.nan)
    ok = np.zeros(len(asofs), dtype=bool)
    if minute.empty:
        return sequences, ok
    for i, asof in enumerate(asofs):
        j = int(np.searchsorted(close_time, int(asof), side="right") - 1)
        if j + 1 < seq_len or j < 0:
            continue
        if int(close_time[j]) > int(asof):
            continue
        window = data[j - seq_len + 1 : j + 1]
        segs = segment[j - seq_len + 1 : j + 1]
        if segs[0] != segs[-1] or not np.isfinite(window).all():
            continue
        sequences[i] = window
        ok[i] = True
    return sequences, ok


def _enrich_minute(usable: pd.DataFrame) -> pd.DataFrame:
    if usable.empty:
        return usable
    parts = [_minute_segment(part.copy()) for _, part in usable.groupby("segment", sort=True)]
    return pd.concat(parts, ignore_index=True)


def _enrich_fast(usable: pd.DataFrame) -> pd.DataFrame:
    if usable.empty:
        return usable
    parts = []
    for _, part in usable.groupby("segment", sort=True):
        close = part["close"].to_numpy(dtype=float)
        high = part["high"].to_numpy(dtype=float)
        low = part["low"].to_numpy(dtype=float)
        ret = np.full(len(part), np.nan)
        ret[1:] = close[1:] / close[:-1] - 1.0
        span = high - low
        part = part.copy()
        part["ret_15s"] = ret
        part["range_15s_norm"] = np.where(close != 0, span / close, np.nan)
        parts.append(part)
    return pd.concat(parts, ignore_index=True)


def _minute_segment(part: pd.DataFrame) -> pd.DataFrame:
    close = part["close"].to_numpy(dtype=float)
    high = part["high"].to_numpy(dtype=float)
    low = part["low"].to_numpy(dtype=float)
    open_ = part["open"].to_numpy(dtype=float)
    ret_1 = _lookback_return(close, 1)
    part["ret_1m"] = ret_1
    part["ret_3m"] = _lookback_return(close, 3)
    part["ret_5m"] = _lookback_return(close, 5)
    part["ret_15m"] = _lookback_return(close, 15)
    part["vol_5m"] = _trailing_std(ret_1, 5)
    part["vol_15m"] = _trailing_std(ret_1, 15)
    changes = np.full(len(close), np.nan)
    changes[1:] = np.diff(close)
    part["price_vol_5"] = _trailing_std(changes, 5)
    ema8 = _ema(close, 8)
    ema21 = _ema(close, 21)
    slope = np.full(len(close), np.nan)
    slope[1:] = ema8[1:] - ema8[:-1]
    part["ema_diff_8_21"] = (ema8 - ema21) / close
    part["ema_slope_8"] = slope / close
    part["ema_dist_8"] = (close - ema8) / close
    part["rsi_14"] = _rsi(close, 14)
    part["roc_10"] = _lookback_return(close, 10)
    atr = _atr(high, low, close, 14)
    part["atr_14"] = atr
    part["atr_14_norm"] = np.where(close != 0, atr / close, np.nan)
    span = high - low
    part["range_norm"] = np.where(close != 0, span / close, np.nan)
    body = np.abs(close - open_)
    upper = high - np.maximum(open_, close)
    lower = np.minimum(open_, close) - low
    with np.errstate(divide="ignore", invalid="ignore"):
        part["body_pct"] = np.where(span > 0, body / span, 0.0)
        part["upper_wick_pct"] = np.where(span > 0, upper / span, 0.0)
        part["lower_wick_pct"] = np.where(span > 0, lower / span, 0.0)
    position, width = _bollinger(close, 20)
    part["bb_position"] = position
    part["bb_width"] = width
    prior_high = _prior_extreme(high, 20, np.max)
    prior_low = _prior_extreme(low, 20, np.min)
    part["dist_prior_high_20"] = (close - prior_high) / close
    part["dist_prior_low_20"] = (close - prior_low) / close
    up, down = _streaks(close)
    part["streak_up"] = up
    part["streak_down"] = down
    valid_trend = np.isfinite(ema8) & np.isfinite(ema21) & np.isfinite(slope)
    part["trend_up"] = _flag((ema8 > ema21) & (slope > 0), valid_trend)
    part["trend_down"] = _flag((ema8 < ema21) & (slope < 0), valid_trend)
    valid_bb = np.isfinite(position)
    part["mean_revert_up"] = _flag(position < 0, valid_bb)
    part["mean_revert_down"] = _flag(position > 1, valid_bb)
    valid_break = np.isfinite(prior_high) & np.isfinite(prior_low)
    part["breakout_up"] = _flag(close > prior_high, valid_break)
    part["breakout_down"] = _flag(close < prior_low, valid_break)
    valid_vol = np.isfinite(part["vol_5m"].to_numpy()) & np.isfinite(part["vol_15m"].to_numpy())
    part["vol_expansion"] = _flag(part["vol_5m"].to_numpy() > part["vol_15m"].to_numpy(), valid_vol)
    part["vol_contraction"] = _flag(part["vol_5m"].to_numpy() < part["vol_15m"].to_numpy(), valid_vol)
    return part


def _lookback_return(close: np.ndarray, lookback: int) -> np.ndarray:
    out = np.full(len(close), np.nan)
    if len(close) <= lookback:
        return out
    base = close[:-lookback]
    out[lookback:] = np.where(base != 0, close[lookback:] / base - 1.0, np.nan)
    return out


def _trailing_std(values: np.ndarray, window: int) -> np.ndarray:
    out = np.full(len(values), np.nan)
    for i in range(window - 1, len(values)):
        chunk = values[i - window + 1 : i + 1]
        if np.isfinite(chunk).all():
            out[i] = float(np.std(chunk, ddof=1))
    return out


def _ema(values: np.ndarray, span: int) -> np.ndarray:
    out = np.full(len(values), np.nan)
    if len(values) < span:
        return out
    alpha = 2.0 / (span + 1.0)
    ema = float(values[0])
    for i, value in enumerate(values):
        ema = float(value) if i == 0 else alpha * float(value) + (1.0 - alpha) * ema
        if i >= span - 1:
            out[i] = ema
    return out


def _rsi(close: np.ndarray, period: int) -> np.ndarray:
    out = np.full(len(close), np.nan)
    if len(close) <= period:
        return out
    delta = np.diff(close)
    gain = np.clip(delta, 0, None)
    loss = np.clip(-delta, 0, None)
    avg_gain = float(gain[:period].mean())
    avg_loss = float(loss[:period].mean())
    out[period] = _rsi_value(avg_gain, avg_loss)
    for i in range(period, len(delta)):
        avg_gain = (avg_gain * (period - 1) + float(gain[i])) / period
        avg_loss = (avg_loss * (period - 1) + float(loss[i])) / period
        out[i + 1] = _rsi_value(avg_gain, avg_loss)
    return out


def _rsi_value(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> np.ndarray:
    out = np.full(len(close), np.nan)
    if len(close) < period:
        return out
    true_range = np.zeros(len(close))
    true_range[0] = high[0] - low[0]
    for i in range(1, len(close)):
        true_range[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1]),
        )
    atr = float(true_range[:period].mean())
    out[period - 1] = atr
    for i in range(period, len(close)):
        atr = (atr * (period - 1) + float(true_range[i])) / period
        out[i] = atr
    return out


def _bollinger(close: np.ndarray, period: int) -> tuple[np.ndarray, np.ndarray]:
    position = np.full(len(close), np.nan)
    width = np.full(len(close), np.nan)
    for i in range(period - 1, len(close)):
        window = close[i - period + 1 : i + 1]
        mid = float(window.mean())
        sd = float(window.std(ddof=1))
        if sd == 0 or mid == 0:
            continue
        upper = mid + 2.0 * sd
        lower = mid - 2.0 * sd
        span = upper - lower
        if span == 0:
            continue
        position[i] = (close[i] - lower) / span
        width[i] = span / mid
    return position, width


def _prior_extreme(values: np.ndarray, period: int, reducer) -> np.ndarray:
    out = np.full(len(values), np.nan)
    for i in range(period, len(values)):
        out[i] = float(reducer(values[i - period : i]))
    return out


def _streaks(close: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    up = np.zeros(len(close))
    down = np.zeros(len(close))
    for i in range(1, len(close)):
        if close[i] > close[i - 1]:
            up[i] = up[i - 1] + 1
        elif close[i] < close[i - 1]:
            down[i] = down[i - 1] + 1
    return up, down


def _flag(condition: np.ndarray, valid: np.ndarray) -> np.ndarray:
    out = np.full(len(condition), np.nan)
    out[valid] = condition[valid].astype(float)
    return out
