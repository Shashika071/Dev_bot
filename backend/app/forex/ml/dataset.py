"""Causal 15m features and candle-aligned 4h Rise/Fall proxy labels.

Labels are an explicit M15 candle proxy for Deriv Rise/Fall, not tick-faithful
contract settlement. Features use only bars whose close time is at or before
the decision timestamp (same rule in training and live inference).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from app.analysis.indicators import frame, indicator_snapshot

DURATION = 14400
STEP_15M = 900
STEP_1H = 3600
STEP_4H = 14400
MIN_BARS = 80
# Reject labels when entry/exit candle alignment drifts beyond one M15 bar.
MAX_ENTRY_OPEN_AGE = STEP_15M
MAX_EXIT_CLOSE_LAG = STEP_15M
LABEL_KIND = "candle_proxy_m15_rise_fall"

FEATURE_COLUMNS = [
    "ret_1", "ret_4", "ret_16", "rsi_15m", "atr_15m", "ema20_dist_atr", "ema50_dist_atr",
    "macd_hist_15m", "adx_15m", "bb_width", "range_pos", "h1_ret", "h4_ret",
]


def _as_frame(rows: list[dict]) -> pd.DataFrame:
    df = frame(rows)
    if df.empty:
        return df
    return df.sort_values("epoch").drop_duplicates("epoch", keep="last").reset_index(drop=True)


def _closed_mask(df: pd.DataFrame, decision_epoch: int, step: int) -> pd.Series:
    """True when the candle's close time is at or before the decision."""
    return df["epoch"] + int(step) <= int(decision_epoch)


def _close_return_closed(df: pd.DataFrame, decision_epoch: int, step: int, lookback_bars: int) -> float | None:
    usable = df[_closed_mask(df, decision_epoch, step)]
    if len(usable) <= lookback_bars:
        return None
    now = float(usable.iloc[-1]["close"])
    past = float(usable.iloc[-1 - lookback_bars]["close"])
    if past <= 0 or now <= 0 or not math.isfinite(now) or not math.isfinite(past):
        return None
    return now / past - 1.0


def _safe_float(value, default: float | None = None) -> float | None:
    if value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _feature_row(m15: pd.DataFrame, h1: pd.DataFrame, h4: pd.DataFrame, index: int) -> dict | None:
    if index < MIN_BARS - 1:
        return None
    window = m15.iloc[: index + 1]
    last = window.iloc[-1]
    epoch = int(last["epoch"])
    decision = epoch + STEP_15M
    snap = indicator_snapshot(window.to_dict("records"))["last"]
    atr14 = _safe_float(snap.get("atr"))
    rsi = _safe_float(snap.get("rsi"))
    if atr14 is None or atr14 <= 0 or rsi is None:
        return None
    close = float(last["close"])
    ema20 = _safe_float(snap.get("ema20"), close) or close
    ema50 = _safe_float(snap.get("ema50"), close) or close
    h1_ret = _close_return_closed(h1, decision, STEP_1H, 4)
    h4_ret = _close_return_closed(h4, decision, STEP_4H, 3)
    if h1_ret is None or h4_ret is None:
        return None
    prior_high = float(window.iloc[-21:-1]["high"].max())
    prior_low = float(window.iloc[-21:-1]["low"].min())
    bb_upper = _safe_float(snap.get("bb_upper"))
    bb_lower = _safe_float(snap.get("bb_lower"))
    bb_middle = _safe_float(snap.get("bb_middle"))
    bb_width = 0.0
    if bb_upper is not None and bb_lower is not None and bb_middle not in (None, 0):
        bb_width = float((bb_upper - bb_lower) / max(abs(bb_middle), 1e-12))
    return {
        "decision_epoch": decision,
        "entry_epoch": decision + 5,
        "expiry_epoch": decision + 5 + DURATION,
        "reference_price": close,
        "label_kind": LABEL_KIND,
        "ret_1": close / float(window.iloc[-2]["close"]) - 1.0,
        "ret_4": close / float(window.iloc[-5]["close"]) - 1.0,
        "ret_16": close / float(window.iloc[-17]["close"]) - 1.0,
        "rsi_15m": rsi,
        "atr_15m": atr14,
        "ema20_dist_atr": (close - ema20) / atr14,
        "ema50_dist_atr": (close - ema50) / atr14,
        "macd_hist_15m": _safe_float(snap.get("macd_hist"), 0.0) or 0.0,
        "adx_15m": _safe_float(snap.get("adx"), 0.0) or 0.0,
        "bb_width": bb_width,
        "range_pos": (close - prior_low) / max(prior_high - prior_low, 1e-12),
        "h1_ret": h1_ret,
        "h4_ret": h4_ret,
    }


def label_outcome(m15: pd.DataFrame, entry_epoch: int, expiry_epoch: int, direction: str) -> str | None:
    """Candle-aligned Rise/Fall proxy. Ties are losses. Returns None on gaps."""
    priced = label_prices(m15, entry_epoch, expiry_epoch)
    if priced is None:
        return None
    entry_price, exit_price, _meta = priced
    if direction == "RISE":
        return "win" if exit_price > entry_price else "loss"
    return "win" if exit_price < entry_price else "loss"


def label_prices(m15: pd.DataFrame, entry_epoch: int, expiry_epoch: int) -> tuple[float, float, dict] | None:
    """Resolve entry/exit prices for the candle proxy with age/lag bounds."""
    if m15.empty or expiry_epoch <= entry_epoch:
        return None
    # Candle that contains entry: open <= entry < close.
    containing = m15[(m15["epoch"] <= entry_epoch) & (m15["epoch"] + STEP_15M > entry_epoch)]
    if not containing.empty:
        entry_bar = containing.iloc[-1]
        entry_open_age = int(entry_epoch) - int(entry_bar["epoch"])
        if entry_open_age < 0 or entry_open_age > MAX_ENTRY_OPEN_AGE:
            return None
        entry_price = float(entry_bar["open"])
        entry_mode = "in_bar_open"
    else:
        later = m15[m15["epoch"] >= entry_epoch]
        if later.empty:
            return None
        entry_bar = later.iloc[0]
        lag = int(entry_bar["epoch"]) - int(entry_epoch)
        if lag < 0 or lag > MAX_ENTRY_OPEN_AGE:
            return None
        entry_price = float(entry_bar["open"])
        entry_mode = "next_open"
    # First candle whose close is at/after expiry, but not more than one bar late.
    closes = m15["epoch"] + STEP_15M
    exits = m15[(closes >= expiry_epoch) & (closes <= expiry_epoch + MAX_EXIT_CLOSE_LAG)]
    if exits.empty:
        return None
    exit_bar = exits.iloc[0]
    exit_price = float(exit_bar["close"])
    exit_close = int(exit_bar["epoch"]) + STEP_15M
    meta = {
        "label_kind": LABEL_KIND,
        "entry_mode": entry_mode,
        "entry_open_age": int(entry_epoch) - int(entry_bar["epoch"]) if entry_mode == "in_bar_open" else lag,
        "exit_close_lag": exit_close - int(expiry_epoch),
        "entry_bar_epoch": int(entry_bar["epoch"]),
        "exit_bar_epoch": int(exit_bar["epoch"]),
    }
    if not math.isfinite(entry_price) or not math.isfinite(exit_price):
        return None
    return entry_price, exit_price, meta


def build_dataset(candles_15m: list[dict], candles_1h: list[dict], candles_4h: list[dict],
                  *, sample_every: int = 4) -> pd.DataFrame:
    m15 = _as_frame(candles_15m)
    h1 = _as_frame(candles_1h)
    h4 = _as_frame(candles_4h)
    if len(m15) < MIN_BARS + 20 or h1.empty or h4.empty:
        return pd.DataFrame()
    rows = []
    last_usable = len(m15) - int(DURATION / STEP_15M) - 2
    for index in range(MIN_BARS, last_usable, max(1, sample_every)):
        feats = _feature_row(m15, h1, h4, index)
        if not feats:
            continue
        priced = label_prices(m15, feats["entry_epoch"], feats["expiry_epoch"])
        if priced is None:
            continue
        entry_price, exit_price, label_meta = priced
        outcome = "win" if exit_price > entry_price else "loss"
        feats["label_rise"] = 1 if outcome == "win" else 0
        feats["entry_price"] = entry_price
        feats["exit_price"] = exit_price
        feats["exit_close_lag"] = label_meta["exit_close_lag"]
        rows.append(feats)
    return pd.DataFrame(rows)


def features_from_live_frames(frames: dict, *, now: int) -> dict | None:
    """Build one feature row from live scan candles (closed bars only)."""
    m15 = _as_frame([r for r in frames.get("15m", []) if int(r["epoch"]) + STEP_15M <= now])
    h1 = _as_frame([r for r in frames.get("1h", []) if int(r["epoch"]) + STEP_1H <= now])
    h4 = _as_frame([r for r in frames.get("4h", []) if int(r["epoch"]) + STEP_4H <= now])
    if len(m15) < MIN_BARS or h1.empty or h4.empty:
        return None
    return _feature_row(m15, h1, h4, len(m15) - 1)


def feature_vector(row: dict) -> np.ndarray:
    values = []
    for col in FEATURE_COLUMNS:
        number = _safe_float(row.get(col))
        if number is None:
            raise ValueError(f"Non-finite ML feature: {col}")
        values.append(number)
    return np.asarray(values, dtype=float)
