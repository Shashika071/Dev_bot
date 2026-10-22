"""One causal feature/decision implementation shared by live scans and training.

The strategy studies R_100 one-minute Rise/Fall contracts. All timestamps denote
candle opens except decision_epoch/closed_at, which denote completed candle closes.
Only confirmed (two right-hand closed bars) pivots are usable. No tick or exchange
volume, institutional intent, forex session, or economic-news inference is invented
for a synthetic market. Pattern names describe price geometry only.
"""

from __future__ import annotations

import bisect
import gc
import math
import threading
import time
from typing import Any

from app.synthetic.indicators import frama_channel, range_filter, technical_series


INTERVALS = {"1m": 60, "5m": 300, "15m": 900}
ENTRY = "1m"
ENTRY_SECONDS = INTERVALS[ENTRY]
STALE_AFTER = ENTRY_SECONDS + 30
MIN_BARS = 220
MAX_SIGNAL_AGE = 2
REFERENCE_URLS = {
    "frama": "https://www.tradingview.com/script/hskyfPWP-FRAMA-Channel-BigBeluga/",
    "range_filter": "https://www.tradingview.com/script/J8GzFGfD-Range-Filter-Buy-and-Sell-5min-guikroth-version/",
}
# Training and a live scan must not build this at the same time: each pass
# holds the full one-minute history, and the API process is memory limited.
_COMPUTE = threading.RLock()
_TOP_KEYS = (
    "trend", "ema_gap_atr", "return_1_atr", "return_3_atr", "return_6_atr",
    "body_fraction", "upper_wick", "lower_wick", "close_ema20_atr", "rsi14", "rsi",
    "macd_hist", "macd_hist_atr", "macd_change_atr", "atr", "atr_pct", "atr_ratio",
    "engulfing", "pin_bar", "doji", "momentum", "candle_strength", "close", "bars",
    "ema20", "ema50", "frama_position", "frama_slope_atr", "frama_alpha", "frama_dimension",
    "frama_direction", "frama_signal_age", "range_position", "range_slope",
    "range_width_atr", "range_direction", "range_signal_age",
)
_STRUCTURE_KEYS = (
    "direction", "higher_high", "higher_low", "lower_high", "lower_low",
    "bos", "choch", "breakout", "liquidity_sweep", "retest", "equal_highs", "equal_lows",
    "support_distance_atr", "resistance_distance_atr",
)
FEATURE_COLUMNS = [
    "return_1_atr", "return_3_atr", "return_6_atr", "body_fraction", "upper_wick", "lower_wick",
    "ema_gap_atr", "close_ema20_atr", "rsi14", "macd_hist_atr", "macd_change_atr",
    "atr_pct", "atr_ratio", "frama_position", "frama_slope_atr", "frama_alpha",
    "frama_dimension", "frama_direction", "frama_signal_age", "range_position",
    "range_slope", "range_width_atr", "range_direction", "range_signal_age",
    "structure_direction", "higher_high", "higher_low", "lower_high", "lower_low",
    "bos", "choch", "liquidity_sweep", "retest", "breakout", "equal_highs", "equal_lows",
    "support_distance_atr", "resistance_distance_atr", "engulfing", "pin_bar", "doji",
    "momentum", "candle_strength", "m5_trend", "m15_trend",
    "m5_ema_gap_atr", "m15_ema_gap_atr", "m5_rsi14", "m15_rsi14",
    "m5_structure", "m15_structure", "checklist_direction", "rule_eligible",
]


def _direction(value: str | int | float) -> int:
    return 1 if value in ("RISE", "HH_HL", 1) else -1 if value in ("FALL", "LH_LL", -1) else 0


def _label(value: int) -> str:
    return "RISE" if value > 0 else "FALL" if value < 0 else "NO TRADE"


def _one_side(*values: int) -> int:
    """Use a direction only when the present readings do not oppose each other."""
    sides = [int(value) for value in values if value]
    if not sides or any(side * sides[0] < 0 for side in sides):
        return 0
    return sides[0]


def _clean(rows: Any, step: int, now: int) -> tuple[list[dict], int]:
    """Validate OHLC, grid alignment and duplicate conflicts; ignore forming bars."""
    if not isinstance(rows, (list, tuple)):
        return [], 1
    unique: dict[int, dict] = {}
    conflicts: set[int] = set()
    invalid = 0
    for row in rows:
        try:
            epoch_value = float(row["epoch"])
            if not math.isfinite(epoch_value) or epoch_value < 0 or not epoch_value.is_integer():
                invalid += 1
                continue
            epoch = int(epoch_value)
            if epoch + step > now:
                continue
            if epoch % step:
                invalid += 1
                continue
            values = {key: float(row[key]) for key in ("open", "high", "low", "close")}
            if not all(math.isfinite(value) and value > 0 for value in values.values()):
                invalid += 1
                continue
            if not values["low"] <= min(values["open"], values["close"]) <= max(values["open"], values["close"]) <= values["high"]:
                invalid += 1
                continue
            clean = {"epoch": epoch, **values}
            if epoch in unique and unique[epoch] != clean:
                conflicts.add(epoch)
            else:
                unique[epoch] = clean
        except (TypeError, KeyError, ValueError, OverflowError):
            invalid += 1
    return [unique[epoch] for epoch in sorted(unique) if epoch not in conflicts], invalid + len(conflicts)


def _structure_series(rows: list[dict], atr: list[float]) -> list[dict]:
    highs: list[dict] = []
    lows: list[dict] = []
    result = []
    prior_direction = 0
    last_broken_high: int | None = None
    last_broken_low: int | None = None
    last_break: dict | None = None
    for index, row in enumerate(rows):
        scale = max(atr[index], float(row["close"]) * 1e-10)
        if index >= 4:
            pivot_index = index - 2
            pivot = rows[pivot_index]
            neighbors = rows[pivot_index - 2:pivot_index] + rows[pivot_index + 1:index + 1]
            if all(pivot["high"] > item["high"] for item in neighbors):
                highs.append({"index": pivot_index, "epoch": pivot["epoch"], "confirmed_at": row["epoch"], "price": pivot["high"]})
            if all(pivot["low"] < item["low"] for item in neighbors):
                lows.append({"index": pivot_index, "epoch": pivot["epoch"], "confirmed_at": row["epoch"], "price": pivot["low"]})
        hh = int(len(highs) >= 2 and highs[-1]["price"] > highs[-2]["price"])
        lh = int(len(highs) >= 2 and highs[-1]["price"] < highs[-2]["price"])
        hl = int(len(lows) >= 2 and lows[-1]["price"] > lows[-2]["price"])
        ll = int(len(lows) >= 2 and lows[-1]["price"] < lows[-2]["price"])
        direction = 1 if hh and hl else -1 if lh and ll else 0
        close = float(row["close"])
        previous_close = float(rows[index - 1]["close"]) if index else close
        resistance = highs[-1]["price"] if highs else max(item["high"] for item in rows[max(0, index - 20):index] or [row])
        support = lows[-1]["price"] if lows else min(item["low"] for item in rows[max(0, index - 20):index] or [row])
        breakout = 0
        if highs and highs[-1]["index"] != last_broken_high and previous_close <= resistance < close:
            breakout, last_broken_high = 1, highs[-1]["index"]
        elif lows and lows[-1]["index"] != last_broken_low and previous_close >= support > close:
            breakout, last_broken_low = -1, lows[-1]["index"]
        established_direction = prior_direction or direction
        bos = breakout if breakout and established_direction == breakout else 0
        choch = breakout if breakout and established_direction == -breakout else 0
        retest = 0
        if last_break is not None and 0 < index - last_break["index"] <= 8:
            level, side = last_break["price"], last_break["direction"]
            if side > 0 and row["low"] <= level + 0.2 * scale and close > level and close > row["open"]:
                retest = 1
            elif side < 0 and row["high"] >= level - 0.2 * scale and close < level and close < row["open"]:
                retest = -1
        if breakout:
            last_break = {"index": index, "price": resistance if breakout > 0 else support, "direction": breakout}
        sweep_up = bool(highs and row["high"] > resistance and close < resistance)
        sweep_down = bool(lows and row["low"] < support and close > support)
        sweep = (1 if sweep_down else 0) - (1 if sweep_up else 0)
        equal_highs = int(len(highs) >= 2 and abs(highs[-1]["price"] - highs[-2]["price"]) <= 0.15 * scale)
        equal_lows = int(len(lows) >= 2 and abs(lows[-1]["price"] - lows[-2]["price"]) <= 0.15 * scale)
        result.append({
            "direction": direction, "name": "HH_HL" if direction > 0 else "LH_LL" if direction < 0 else "SIDEWAYS",
            "higher_high": hh, "higher_low": hl, "lower_high": lh, "lower_low": ll,
            "bos": bos, "choch": choch, "breakout": breakout, "liquidity_sweep": sweep,
            "retest": retest, "equal_highs": equal_highs, "equal_lows": equal_lows,
            "support": support, "resistance": resistance,
            "support_distance_atr": (close - support) / scale, "resistance_distance_atr": (resistance - close) / scale,
        })
        if direction:
            prior_direction = direction
    return result


def _frame_series(rows: list[dict], step: int) -> dict:
    technical = technical_series(rows)
    structures = _structure_series(rows, technical["atr"])
    framas = frama_channel(rows) if step == ENTRY_SECONDS else []
    filters = range_filter(rows) if step == ENTRY_SECONDS else []
    columns = {key: [] for key in _TOP_KEYS}
    structure_columns = {key: [] for key in _STRUCTURE_KEYS}
    closes: list[int] = []
    contiguous_values: list[int] = []
    last = None
    contiguous = 0
    for index, row in enumerate(rows):
        contiguous = contiguous + 1 if not index or row["epoch"] - rows[index - 1]["epoch"] == step else 1
        scale = max(technical["atr"][index], row["close"] * 1e-10)
        span = max(row["high"] - row["low"], row["close"] * 1e-10)
        body = row["close"] - row["open"]
        body_fraction = body / span
        upper_wick = (row["high"] - max(row["open"], row["close"])) / span
        lower_wick = (min(row["open"], row["close"]) - row["low"]) / span
        ret3 = (row["close"] - rows[max(0, index - 3)]["close"]) / scale
        trend = 1 if technical["ema20"][index] > technical["ema50"][index] + 0.1 * scale and row["close"] > technical["ema20"][index] else (
            -1 if technical["ema20"][index] < technical["ema50"][index] - 0.1 * scale and row["close"] < technical["ema20"][index] else 0)
        macd_hist = technical["macd_hist"][index]
        momentum = 1 if body_fraction >= 0.35 and ret3 > 0.1 and macd_hist > 0 else (
            -1 if body_fraction <= -0.35 and ret3 < -0.1 and macd_hist < 0 else 0)
        previous = rows[max(0, index - 1)]
        engulfing = 0
        if index and body > 0 and previous["close"] < previous["open"] and row["open"] <= previous["close"] and row["close"] >= previous["open"]:
            engulfing = 1
        elif index and body < 0 and previous["close"] > previous["open"] and row["open"] >= previous["close"] and row["close"] <= previous["open"]:
            engulfing = -1
        pin = 1 if lower_wick >= 0.6 and upper_wick <= 0.15 else -1 if upper_wick >= 0.6 and lower_wick <= 0.15 else 0
        snap = {
            "closed_at": int(row["epoch"]) + step, "bars": index + 1, "contiguous": contiguous,
            "close": row["close"], "trend": trend, "structure": structures[index],
            **{key: values[index] for key, values in technical.items()},
            "ema_gap_atr": (technical["ema20"][index] - technical["ema50"][index]) / scale,
            "return_1_atr": (row["close"] - previous["close"]) / scale,
            "return_3_atr": ret3,
            "return_6_atr": (row["close"] - rows[max(0, index - 6)]["close"]) / scale,
            "body_fraction": body_fraction, "upper_wick": upper_wick, "lower_wick": lower_wick,
            "close_ema20_atr": (row["close"] - technical["ema20"][index]) / scale,
            "rsi14": technical["rsi"][index], "macd_hist_atr": macd_hist / scale,
            "macd_change_atr": (macd_hist - technical["macd_hist"][max(0, index - 1)]) / scale,
            "atr_pct": technical["atr"][index] / row["close"],
            "atr_ratio": technical["atr"][index] / max(technical["atr"][max(0, index - 20)], row["close"] * 1e-10),
            "engulfing": engulfing, "pin_bar": pin, "doji": int(abs(body_fraction) <= 0.1),
            "momentum": momentum, "candle_strength": body / scale,
        }
        if step == ENTRY_SECONDS:
            frama, rf = framas[index], filters[index]
            midpoint = frama["mid"] if frama["mid"] is not None else row["close"]
            previous_midpoint = framas[max(0, index - 1)]["mid"]
            snap.update({
                "frama": frama, "range_filter": rf,
                "frama_position": (row["close"] - midpoint) / scale,
                "frama_slope_atr": (midpoint - (previous_midpoint if previous_midpoint is not None else midpoint)) / scale,
                "frama_alpha": frama["alpha"], "frama_dimension": frama["dimension"],
                "frama_direction": _direction(frama["direction"]),
                "frama_signal_age": min(frama["signal_age"], 999) if frama["signal_age"] is not None else 999,
                "range_position": (row["close"] - rf["line"]) / scale, "range_slope": rf["slope"],
                "range_width_atr": rf["range"] / scale, "range_direction": _direction(rf["direction"]),
                "range_signal_age": min(rf["signal_age"], 999) if rf["signal_age"] is not None else 999,
            })
        structure = snap["structure"]
        for key in _TOP_KEYS:
            value = snap.get(key)
            columns[key].append(0.0 if value is None else float(value))
        for key in _STRUCTURE_KEYS:
            value = structure.get(key)
            structure_columns[key].append(0.0 if value is None else float(value))
        closes.append(int(snap["closed_at"]))
        contiguous_values.append(int(snap["contiguous"]))
        last = snap
    # Drop every bar except the latest. Training reads the numeric columns;
    # keeping a dict per candle exhausts the API memory limit and the process dies.
    del framas, filters, structures
    if len(rows) > 5000:
        gc.collect()
    return {
        "closes": closes, "columns": columns, "structure": structure_columns,
        "contiguous": contiguous_values, "last": last,
    }


def _assemble(entry: dict, m5: dict, m15: dict) -> tuple[dict, list[str]]:
    structure = entry["structure"]
    features = {column: float(entry.get(column, structure.get(column, 0))) for column in FEATURE_COLUMNS}
    features["structure_direction"] = float(structure["direction"])
    for label, snapshot in (("m5", m5), ("m15", m15)):
        features[f"{label}_trend"] = float(snapshot["trend"])
        features[f"{label}_ema_gap_atr"] = float(snapshot["ema_gap_atr"])
        features[f"{label}_rsi14"] = float(snapshot["rsi"])
        features[f"{label}_structure"] = float(snapshot["structure"]["direction"])
    candidate = int(entry["frama_direction"]) if entry["frama_direction"] == entry["range_direction"] else 0
    blockers = []
    if not candidate:
        blockers.append("FRAMA and Range Filter do not both confirm the same direction.")
    else:
        for name, snapshot in (("5m", m5), ("15m", m15)):
            if snapshot["trend"] != candidate:
                blockers.append(f"{name} trend does not confirm {_label(candidate)}.")
        if min(entry["frama_signal_age"], entry["range_signal_age"]) > MAX_SIGNAL_AGE:
            blockers.append(f"No fresh FRAMA breakout or Range Filter cross within {MAX_SIGNAL_AGE + 1} closed candles.")
        if entry["momentum"] != candidate:
            blockers.append("Candle body, three-bar momentum and MACD do not confirm the direction.")
    features["checklist_direction"] = float(candidate)
    features["rule_eligible"] = float(not blockers)
    return features, blockers


def _prepared(frames: dict, now: int) -> tuple[dict, list[str]]:
    errors = []
    clean_frames = {}
    for name, step in INTERVALS.items():
        rows, invalid = _clean(frames.get(name, []), step, now)
        clean_frames[name] = rows
        if invalid:
            errors.append(f"{name}: {invalid} malformed or conflicting closed candle(s).")
    # A higher-timeframe bar cannot be used after the last entry candle's close.
    asof = clean_frames[ENTRY][-1]["epoch"] + ENTRY_SECONDS if clean_frames[ENTRY] else now
    prepared = {}
    for name, step in INTERVALS.items():
        prepared[name] = _frame_series([row for row in clean_frames[name] if row["epoch"] + step <= asof], step)
    return prepared, errors


def _view(series: dict, index: int) -> dict:
    """One candle's features without retaining a dict for every candle."""
    snap = {key: values[index] for key, values in series["columns"].items()}
    snap["structure"] = {key: values[index] for key, values in series["structure"].items()}
    snap["closed_at"] = series["closes"][index]
    snap["contiguous"] = series["contiguous"][index]
    return snap


def _select(prepared: dict, decision: int) -> tuple[dict | None, list[str]]:
    selected = {}
    errors = []
    for name, step in INTERVALS.items():
        series = prepared[name]
        index = bisect.bisect_right(series["closes"], decision) - 1
        if index < 0:
            errors.append(f"{name}: no closed candles.")
            continue
        contiguous = series["contiguous"][index]
        closed_at = series["closes"][index]
        selected[name] = series["last"] if index == len(series["closes"]) - 1 and series["last"] is not None else _view(series, index)
        if contiguous < MIN_BARS:
            errors.append(f"{name}: need {MIN_BARS} consecutive closed candles; have {contiguous}.")
        if decision - closed_at >= step:
            errors.append(f"{name}: closed-candle history is stale.")
    return (selected if len(selected) == len(INTERVALS) else None), errors


def features_from_frames(frames: dict[str, list[dict]], now: int) -> dict | None:
    """Return finite causal model inputs, or None for bad/stale/short history."""
    if not _COMPUTE.acquire(blocking=False):
        return None
    try:
        return _features_from_frames(frames, now)
    finally:
        _COMPUTE.release()


def _features_from_frames(frames: dict[str, list[dict]], now: int) -> dict | None:
    prepared, invalid = _prepared(frames, int(now))
    if invalid or not prepared[ENTRY]["closes"]:
        return None
    decision = prepared[ENTRY]["closes"][-1]
    selected, errors = _select(prepared, decision)
    if selected is None or errors or int(now) - decision >= STALE_AFTER:
        return None
    features, _ = _assemble(selected[ENTRY], selected["5m"], selected["15m"])
    return features if all(math.isfinite(value) for value in features.values()) else None


def feature_table(frames: dict[str, list[dict]], now: int | None = None,
                  progress: dict | None = None, on_progress=None):
    """Efficient causal batch features, with exactly the live technical rule gates.

    Output columns are decision_epoch and FEATURE_COLUMNS. The caller labels the
    *next* candle's open to close and performs time/purge-aware validation. There
    are no outcome labels here. Gaps reset the required consecutive-bar warmup.
    """
    import pandas as pd

    if now is None:
        try:
            now = max((int(row["epoch"]) + INTERVALS[name]
                       for name in INTERVALS for row in frames.get(name, [])), default=0)
        except (TypeError, ValueError, KeyError, OverflowError):
            return pd.DataFrame(columns=["decision_epoch", *FEATURE_COLUMNS])
    with _COMPUTE:
        return _feature_table(frames, int(now), progress, on_progress)


def _feature_table(frames, now: int, progress, on_progress):
    import pandas as pd

    prepared, invalid = _prepared(frames, int(now))
    if invalid:
        return pd.DataFrame(columns=["decision_epoch", *FEATURE_COLUMNS])
    records = []
    closes = prepared[ENTRY]["closes"]
    total = len(closes)
    for count, decision in enumerate(closes):
        if count % 5000 == 0:
            if progress is not None:
                progress["progress"] = f"Building features {count} of {total} closed 1-minute candles"
            if on_progress is not None:
                on_progress()
            time.sleep(0.001)
        selected, errors = _select(prepared, decision)
        if selected is None or errors:
            continue
        features, _ = _assemble(selected[ENTRY], selected["5m"], selected["15m"])
        if all(math.isfinite(value) for value in features.values()):
            records.append({"decision_epoch": decision, **features})
    return pd.DataFrame(records, columns=["decision_epoch", *FEATURE_COLUMNS])


def chart_overlays(candles: list[dict]) -> dict:
    """Chart lines and the buy/sell crosses from FRAMA and the Range Filter."""
    frama = frama_channel(candles)
    filtered = range_filter(candles)
    signals = []
    for source, rows in (("FRAMA", frama), ("Range Filter", filtered)):
        for row in rows:
            if row["cross"] not in ("RISE", "FALL"):
                continue
            signals.append({
                "epoch": int(row["epoch"]),
                "side": "BUY" if row["cross"] == "RISE" else "SELL",
                "source": source,
            })
    return {
        **{name: [{"epoch": row["epoch"], "value": row[key]} for row in frama if row[key] is not None]
           for name, key in (("frama", "mid"), ("frama_upper", "upper"), ("frama_lower", "lower"))},
        "range_filter": [{"epoch": row["epoch"], "value": row["line"]} for row in filtered],
        "signals": signals,
    }


def _busy_analysis() -> dict:
    return {
        "symbol": "R_100", "direction": "NO TRADE", "eligible": False,
        "blockers": ["Training is using the candle history. This scan was skipped."],
        "decision_epoch": None, "expiry_seconds": ENTRY_SECONDS, "timeframe": ENTRY,
        "indicators": {}, "timeframes": {}, "factors": [], "features": {},
        "levels": [], "events": [],
    }


def analyze_synthetic(frames: dict[str, list[dict]], now: int) -> dict:
    """Evaluate the current closed candle. Skips instead of doubling memory during training."""
    if not _COMPUTE.acquire(blocking=False):
        return _busy_analysis()
    try:
        return _analyze_synthetic(frames, now)
    finally:
        _COMPUTE.release()


def _analyze_synthetic(frames: dict[str, list[dict]], now: int) -> dict:
    """Evaluate the current closed candle; model approval remains the caller's gate."""
    prepared, errors = _prepared(frames, int(now))
    decision = prepared[ENTRY]["closes"][-1] if prepared[ENTRY]["closes"] else None
    selected, history_errors = _select(prepared, decision if decision is not None else int(now))
    errors.extend(history_errors)
    if decision is not None and int(now) - decision >= STALE_AFTER:
        errors.append("1m: latest decision candle is stale.")
    result: dict[str, Any] = {
        "symbol": "R_100", "direction": "NO TRADE", "eligible": False, "blockers": list(errors),
        "decision_epoch": decision, "expiry_seconds": ENTRY_SECONDS, "timeframe": ENTRY,
        "indicators": {}, "timeframes": {}, "factors": [], "features": {},
        "levels": [], "events": [],
        "indicator_implementation": "Independent Python adaptation; exact TradingView Pine parity is unverified.",
        "indicator_references": REFERENCE_URLS,
    }
    if selected is None:
        result["factors"] = [{"id": "history", "name": "Closed-candle history", "status": "BLOCK", "detail": " ".join(errors)}]
        return result
    entry, m5, m15 = selected[ENTRY], selected["5m"], selected["15m"]
    features, rule_errors = _assemble(entry, m5, m15)
    result["indicators"] = {"frama": entry["frama"], "range_filter": entry["range_filter"]}
    result["timeframes"] = {
        name: {**{key: snapshot[key] for key in ("close", "ema20", "ema50", "rsi", "macd_hist", "atr", "closed_at", "bars")},
               "trend": _label(snapshot["trend"]) if snapshot["trend"] else "SIDEWAYS", "structure": snapshot["structure"]["name"]}
        for name, snapshot in selected.items()
    }
    candidate = int(features["checklist_direction"])
    if not errors:
        result["features"] = features
    result["blockers"].extend(rule_errors)
    result["eligible"] = not result["blockers"]
    result["direction"] = _label(candidate) if result["eligible"] else "NO TRADE"
    result["candidate_direction"] = _label(candidate)
    signal_epochs = [item.get("signal_epoch") for item in (entry["frama"], entry["range_filter"])]
    result["signal_epoch"] = max((epoch for epoch in signal_epochs if epoch is not None), default=None)
    structure = entry["structure"]
    result["levels"] = [{"type": name, "price": structure[name]} for name in ("support", "resistance")]
    result["events"] = [
        {"type": name, "direction": _label(structure[name]), "epoch": decision}
        for name in ("bos", "choch", "breakout", "retest", "liquidity_sweep") if structure[name]
    ]
    frama_ok = bool(candidate)
    recent = min(entry["frama_signal_age"], entry["range_signal_age"]) <= MAX_SIGNAL_AGE
    pattern_names = [name for name in ("engulfing", "pin_bar", "doji") if entry[name]]
    span = float(structure["resistance"]) - float(structure["support"])
    level_side = 0
    if span > 0:
        close = float(entry["close"])
        if close <= float(structure["support"]) + 0.25 * span:
            level_side = 1
        elif close >= float(structure["resistance"]) - 0.25 * span:
            level_side = -1
    timeframe_side = int(m5["trend"]) if m5["trend"] and m5["trend"] == m15["trend"] else 0
    event_side = _one_side(structure["bos"], structure["choch"])
    breakout_side = _one_side(structure["breakout"], structure["retest"])
    macd_side = 1 if entry["macd_hist"] > 0 else -1 if entry["macd_hist"] < 0 else 0

    def factor(key: str, name: str, ok: bool, detail: str, side: int = 0, *, waiting: bool = False) -> dict:
        return {
            "id": key, "name": name, "status": "PASS" if ok else "WAIT" if waiting else "BLOCK",
            "detail": detail, "side": _label(side) if side else "WAIT",
        }

    result["factors"] = [
        factor("history", "Closed-candle history", not errors, " ".join(errors) if errors else f"At least {MIN_BARS} consecutive closed bars on every timeframe."),
        factor("frama", "FRAMA channel", bool(entry["frama_direction"]), f"Price direction: {entry['frama']['direction']}; HL2 FRAMA 26, range bands 200 × 1.5.", int(entry["frama_direction"])),
        factor("range_filter", "Range Filter", bool(entry["range_direction"]), f"Price direction: {entry['range_filter']['direction']}; period 100, multiplier 3.", int(entry["range_direction"])),
        factor("agreement", "Both indicators agree", frama_ok, f"Combined direction: {_label(candidate)}.", candidate),
        factor("fresh_signal", "Recent entry signal", bool(candidate and recent), "Require a same-direction channel breakout or filter cross within the last three closed candles."),
        factor("timeframes", "5m and 15m trend", bool(candidate and m5["trend"] == m15["trend"] == candidate), f"5m: {_label(m5['trend'])}; 15m: {_label(m15['trend'])}.", timeframe_side),
        factor("momentum", "Candle and momentum confirmation", bool(candidate and entry["momentum"] == candidate), f"Body {entry['body_fraction']:.0%} of range; three-bar move {entry['return_3_atr']:.2f} ATR; MACD histogram {entry['macd_hist']:.5f}.", int(entry["momentum"])),
        factor("trend_structure", "Market structure", bool(structure["direction"]), f"{structure['name']}; confirmed HH {structure['higher_high']}, HL {structure['higher_low']}, LH {structure['lower_high']}, LL {structure['lower_low']}.", int(structure["direction"]), waiting=not structure["direction"]),
        factor("bos_choch", "BOS / CHoCH", bool(structure["bos"] or structure["choch"]), f"BOS {_label(structure['bos'])}; CHoCH {_label(structure['choch'])}.", event_side, waiting=not (structure["bos"] or structure["choch"])),
        factor("levels", "Support / resistance", bool(level_side), f"Support {structure['support']:.4f}; resistance {structure['resistance']:.4f}.", level_side, waiting=level_side == 0),
        factor("liquidity", "Equal levels and liquidity sweep", bool(structure["liquidity_sweep"]), f"Sweep {_label(structure['liquidity_sweep'])}; equal highs {structure['equal_highs']}, equal lows {structure['equal_lows']}. Price geometry only.", int(structure["liquidity_sweep"] or 0), waiting=not structure["liquidity_sweep"]),
        factor("breakout_retest", "Breakout / retest", bool(structure["breakout"] or structure["retest"]), f"Breakout {_label(structure['breakout'])}; retest {_label(structure['retest'])}.", breakout_side, waiting=not (structure["breakout"] or structure["retest"])),
        factor("candles", "Candlestick patterns", bool(pattern_names), ", ".join(pattern_names) if pattern_names else "No engulfing, pin bar or doji on the latest candle.", waiting=not pattern_names),
        factor("ema", "EMA 20 / EMA 50", bool(entry["trend"]), f"EMA 20 {entry['ema20']:.4f}; EMA 50 {entry['ema50']:.4f}.", int(entry["trend"]), waiting=not entry["trend"]),
        factor("rsi", "RSI momentum", True, f"RSI 14: {entry['rsi']:.1f}.", waiting=True),
        factor("macd", "MACD", bool(macd_side), f"MACD histogram: {entry['macd_hist']:.5f}.", macd_side, waiting=not macd_side),
        factor("atr", "ATR volatility", entry["atr"] > 0, f"ATR 14: {entry['atr']:.5f}; {entry['atr_pct']:.3%} of price.", waiting=entry["atr"] <= 0),
    ]
    return result
