"""Closed-bar, multi-timeframe Forex analysis for four-hour Rise/Fall research.

The score is an uncalibrated checklist score, never a probability or a return
forecast. Execution, account checks, news, quotes and exposure belong to the
caller. Correlated indicators share a scoring group rather than separate votes.
"""

from __future__ import annotations

import math
from statistics import median

from app.analysis.breakouts import breakout_events
from app.analysis.candle_patterns import detect_at
from app.analysis.chart_patterns import chart_patterns
from app.analysis.indicators import atr, frame, indicator_snapshot
from app.analysis.structure import structure_state, support_resistance, swings


INTERVALS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
ALIASES = {"1m": "M1", "5m": "M5", "15m": "M15", "1h": "H1", "4h": "H4", "1d": "D1"}
MIN_BARS = 60
FACTOR_NAMES = {
    1: "Market trend", 2: "Market structure", 3: "Support and resistance",
    4: "Supply and demand", 5: "Liquidity zones", 6: "Breakouts",
    7: "False breakouts", 8: "Momentum", 9: "Volatility",
    10: "Candlestick patterns", 11: "Chart patterns", 12: "Multi-timeframe analysis",
    13: "Moving averages (EMA/SMA)", 14: "RSI", 15: "MACD", 16: "ATR",
    17: "Bollinger Bands", 18: "ADX", 19: "Fibonacci retracement",
    20: "Volume / tick volume", 29: "Currency strength",
}


def _closed(rows: list[dict], step: int, now: int) -> tuple[list[dict], int]:
    """Reject malformed closed OHLC; never replace absent history with future bars."""
    unique: dict[int, dict] = {}
    conflicts: set[int] = set()
    invalid = 0
    for row in rows:
        try:
            epoch_number = float(row["epoch"])
            if not math.isfinite(epoch_number) or not epoch_number.is_integer() or epoch_number < 0:
                invalid += 1
                continue
            epoch = int(epoch_number)
            if epoch + step > now:
                continue
            values = {key: float(row[key]) for key in ("open", "high", "low", "close")}
            if not all(math.isfinite(value) and value > 0 for value in values.values()):
                invalid += 1
                continue
            if not values["low"] <= min(values["open"], values["close"]) <= max(values["open"], values["close"]) <= values["high"]:
                invalid += 1
                continue
            candle = {"epoch": epoch, **values}
            # Tick volume must be supplied explicitly; OHLC is not traded volume.
            if row.get("tick_volume") is not None:
                tick_volume = float(row["tick_volume"])
                if math.isfinite(tick_volume) and tick_volume >= 0:
                    candle["tick_volume"] = tick_volume
            if epoch in unique and any(unique[epoch][key] != candle[key] for key in values):
                conflicts.add(epoch)
            else:
                unique[epoch] = candle
        except (TypeError, ValueError, KeyError, OverflowError):
            invalid += 1
    return [unique[epoch] for epoch in sorted(unique) if epoch not in conflicts], invalid + len(conflicts)


def _snapshot(rows: list[dict]) -> dict:
    last = indicator_snapshot(rows)["last"]
    # The shared RSI helper returns NaN when there are no losses. Handle the
    # well-defined one-sided and flat limits locally without changing touch bots.
    changes = [b["close"] - a["close"] for a, b in zip(rows[-15:-1], rows[-14:])]
    gains = sum(max(0.0, change) for change in changes)
    losses = sum(max(0.0, -change) for change in changes)
    if losses == 0:
        last["rsi"] = 100.0 if gains else 50.0
    elif gains == 0:
        last["rsi"] = 0.0
    return last


def _trend(last: dict, rows: list[dict]) -> str:
    band = max(last.get("atr") or 0, 1e-12)
    ema20, ema50 = last.get("ema20"), last.get("ema50")
    if ema20 is None or ema50 is None:
        return "SIDEWAYS"
    move = rows[-1]["close"] - rows[-6]["close"]
    if ema20 > ema50 + 0.15 * band and last["close"] > ema20 and move > 0.1 * band:
        return "RISE"
    if ema20 < ema50 - 0.15 * band and last["close"] < ema20 and move < -0.1 * band:
        return "FALL"
    return "SIDEWAYS"


def _public_events(events: list[dict]) -> list[dict]:
    # Shared visual pattern detectors have heuristic "confidence" fields; omit
    # them so downstream code cannot mistake those values for win probabilities.
    return [{key: value for key, value in event.items() if key != "confidence"} for event in events]


def _fib(points: list[dict], bias: str) -> dict:
    end_kind = "high" if bias == "RISE" else "low"
    ends = [point for point in points if point["kind"] == end_kind]
    if bias not in {"RISE", "FALL"} or not ends:
        return {"available": False, "levels": []}
    end = ends[-1]
    starts = [point for point in points if point["kind"] != end_kind and point["index"] < end["index"]]
    if not starts:
        return {"available": False, "levels": []}
    start = starts[-1]
    impulse = end["price"] - start["price"]
    if (bias == "RISE" and impulse <= 0) or (bias == "FALL" and impulse >= 0):
        return {"available": False, "levels": []}
    return {
        "available": True, "start": start, "end": end,
        "levels": [{"ratio": ratio, "price": end["price"] - ratio * impulse} for ratio in (0.382, 0.5, 0.618)],
        "method": "Retracement of the latest completed, confirmed swing impulse; a price heuristic.",
    }


def _supply_demand(rows: list[dict]) -> list[dict]:
    """Prior candle ranges preceding a large displacement; no order-book claim."""
    if len(rows) < 20:
        return []
    ranges = [item["high"] - item["low"] for item in rows]
    zones = []
    for index in range(max(15, len(rows) - 50), len(rows)):
        typical = median(ranges[index - 14:index])
        bar, base = rows[index], rows[index - 1]
        move = bar["close"] - bar["open"]
        if typical > 0 and abs(move) >= typical * 1.5:
            kind = "demand" if move > 0 else "supply"
            later = rows[index + 1:]
            invalidated = any(item["close"] < base["low"] if kind == "demand" else item["close"] > base["high"] for item in later)
            if not invalidated:
                zones.append({"type": kind, "low": base["low"], "high": base["high"], "epoch": base["epoch"], "confirmed_at": bar["epoch"], "heuristic": True})
    return zones[-8:]


def chart_overlays(candles: list[dict]) -> dict:
    """Factor layers for the live chart from the same closed candles shown.

    Independent of the last scanner run so overlays work before Scan now.
    """
    rows = sorted(
        (row for row in candles if row and int(row.get("epoch") or 0) > 0),
        key=lambda row: int(row["epoch"]),
    )
    if len(rows) < 30:
        return {
            "levels": {
                "support_resistance": [],
                "fibonacci": {"available": False, "levels": []},
                "supply_demand": [],
                "liquidity": [],
            },
            "events": [],
        }
    points = swings(rows)
    structure = structure_state(points)
    bias = "RISE" if structure == "HH_HL" else "FALL" if structure == "LH_LL" else "WAIT"
    zones = support_resistance(rows, points)
    fib = _fib(points, bias)
    if not fib["available"] and points:
        # Show retracement of the latest swing even without a clear bias so the UI has levels.
        fib = _fib(points, "RISE" if points[-1]["kind"] == "high" else "FALL")
    supply_demand = _supply_demand(rows)
    liquidity = [
        {
            "type": "prior_swing_high" if point["kind"] == "high" else "prior_swing_low",
            "price": point["price"],
            "epoch": point["epoch"],
            "heuristic": True,
        }
        for point in points[-10:]
    ]
    candle_events = detect_at(rows, allow_gaps=True)
    charts = chart_patterns(rows)
    last_epoch = int(rows[-1]["epoch"])
    breaks = [event for event in breakout_events(rows) if int(event.get("epoch") or 0) == last_epoch]
    return {
        "levels": {
            "support_resistance": _public_events(zones),
            "fibonacci": fib,
            "supply_demand": supply_demand,
            "liquidity": liquidity,
        },
        "events": _public_events(candle_events[-12:] + charts[-8:] + breaks),
    }


def analyze_forex(candles_by_interval: dict, *, symbol: str, now: int, duration_seconds: int = 14400) -> dict:
    """Return a technical candidate; ``ready`` does not authorize execution.

    Input epochs are candle-open times. Canonical interval keys (1m..1d), M1..D1,
    and interval seconds are accepted. Every frame needs 60 valid closed bars.
    M1/M5 are context only; D1/H4 bias, H1 confirmation and an M15 price setup
    drive the candidate. News/execution/risk gates must still run in the caller.
    """
    now = int(now)
    factors = {number: {"id": number, "name": name, "status": "unavailable", "detail": "Insufficient closed-candle evidence."} for number, name in FACTOR_NAMES.items()}
    closed: dict[str, list[dict]] = {}
    timeframes: dict[str, dict] = {}
    blockers: list[str] = []
    reasons: list[str] = []
    indicators: dict[str, dict] = {}
    for name, step in INTERVALS.items():
        source = next((candles_by_interval[key] for key in (name, ALIASES[name], step, str(step)) if key in candles_by_interval), [])
        rows, invalid = _closed(source if isinstance(source, list) else [], step, now)
        rows = rows[-300:]
        closed[name] = rows
        closed_at = rows[-1]["epoch"] + step if rows else None
        age = now - closed_at if closed_at is not None else None
        stale = age is None or age > step + max(30, min(300, step * 0.05))
        valid = len(rows) >= MIN_BARS and not stale and invalid == 0
        timeframes[name] = {
            "label": ALIASES[name], "interval_seconds": step, "closed_bars": len(rows),
            "last_closed_epoch": rows[-1]["epoch"] if rows else None, "closed_at": closed_at,
            "age_seconds": age, "stale": stale, "ready": valid,
            "role": "context" if name in {"1m", "5m"} else "trigger" if name == "15m" else "confirmation" if name == "1h" else "bias",
            "trend": "UNAVAILABLE", "structure": "insufficient", "indicators": {},
        }
        if len(rows) < MIN_BARS:
            blockers.append(f"{ALIASES[name]} needs {MIN_BARS} valid closed candles (received {len(rows)}).")
        if rows and stale:
            blockers.append(f"{ALIASES[name]} closed-candle data is stale.")
        if invalid:
            blockers.append(f"{ALIASES[name]} has {invalid} malformed or conflicting closed candles.")
        if len(rows) >= MIN_BARS:
            last = _snapshot(rows)
            indicators[name] = last
            timeframes[name].update(trend=_trend(last, rows), structure=structure_state(swings(rows)), indicators=last)

    result = {
        "symbol": symbol, "direction": "WAIT", "bias": "WAIT", "score": 0, "ready": False,
        "score_type": "uncalibrated technical checklist score; not a win probability",
        "duration_seconds": duration_seconds, "evaluated_at": now,
        "as_of_epoch": closed["15m"][-1]["epoch"] if closed["15m"] else None,
        "blockers": blockers, "reasons": reasons, "timeframes": timeframes,
        "factors": list(factors.values()), "candles": closed["15m"][-120:], "levels": {},
        "events": [], "score_groups": {}, "analysis_version": "forex_mtf_v1",
        "note": "Indicators support price-action analysis. Supply/demand, liquidity and Fibonacci are price-based heuristics, not observed orders. News, contract availability, payout and risk checks are separate mandatory execution gates.",
    }

    def factor(number: int, status: str, detail: str) -> None:
        factors[number].update(status=status, detail=detail)

    factor(20, "unavailable", "No exchange-wide FX volume or order book is supplied. Tick volume is used only when explicitly present.")
    factor(29, "unavailable", "A single pair cannot establish currency strength; a synchronized cross-pair basket is required.")
    factor(12, "blocked" if blockers else "observed", "; ".join(f"{ALIASES[name]} {item['trend']}" for name, item in timeframes.items()) + ". M1/M5 provide context only.")
    if duration_seconds != 14400:
        blockers.append("This strategy is configured and evaluated for a four-hour expiry only.")
    if len(indicators) != len(INTERVALS):
        return result

    h1, h4, daily, m15 = (indicators[name] for name in ("1h", "4h", "1d", "15m"))
    trends = [timeframes[name]["trend"] for name in ("4h", "1d", "1h")]
    bias = trends[0] if trends[0] in {"RISE", "FALL"} and len(set(trends)) == 1 else "WAIT"
    result["bias"] = bias
    if bias == "WAIT":
        blockers.append("D1/H4 trend bias and H1 confirmation do not agree on a directional trend.")
    else:
        reasons.append(f"D1 and H4 agree on {bias}; H1 confirms the same direction.")
    factor(1, "confirmed" if bias != "WAIT" else "blocked", f"H4 {trends[0]}, D1 {trends[1]}, H1 {trends[2]}.")
    structure = {name: timeframes[name]["structure"] for name in ("1h", "4h", "1d")}
    expected_structure = "HH_HL" if bias == "RISE" else "LH_LL"
    opposing_structure = "LH_LL" if bias == "RISE" else "HH_HL"
    structure_match = sum(value == expected_structure for value in structure.values()) if bias != "WAIT" else 0
    structure_conflict = bias != "WAIT" and any(value == opposing_structure for value in structure.values())
    if structure_conflict:
        blockers.append("Confirmed swing structure opposes the higher-timeframe trend.")
    factor(2, "blocked" if structure_conflict else "confirmed" if structure_match else "neutral", "; ".join(f"{ALIASES[name]} {value}" for name, value in structure.items()) + ". Pivots require three closed bars to their right.")

    rows = closed["15m"]
    last, previous = rows[-1], rows[-2]
    band = max(m15["atr"] or 0.0, 1e-12)
    h1_points = swings(closed["1h"])
    zones = support_resistance(closed["1h"], h1_points)
    fib = _fib(h1_points, bias)
    supply_demand = _supply_demand(closed["1h"])
    liquidity = [{"type": "prior_swing_high" if point["kind"] == "high" else "prior_swing_low", "price": point["price"], "epoch": point["epoch"], "heuristic": True} for point in h1_points[-10:]]
    result["levels"] = {"support_resistance": _public_events(zones), "fibonacci": fib, "supply_demand": supply_demand, "liquidity": liquidity}
    nearest_zone = None
    if zones:
        nearest_zone = min(zones, key=lambda zone: abs(zone["price"] - last["close"]))
    factor(3, "observed" if zones else "neutral",
           (f"{len(zones)} H1 support/resistance clusters from repeated confirmed swings. "
            f"Nearest {nearest_zone['type']} ≈ {nearest_zone['price']:.6g} "
            f"({abs(nearest_zone['price'] - last['close']) / band:.2f} ATR from M15 close). "
            "Clusters describe reaction history, not a guaranteed reversal.")
           if nearest_zone else
           "No repeated H1 swing clusters yet; wait for confirmed pivots before treating levels as evidence.")
    factor(4, "heuristic", f"{len(supply_demand)} unbroken H1 displacement-origin zones. Price-based supply/demand proxies, not observed resting orders.")
    factor(5, "heuristic", f"{len(liquidity)} recent H1 swing levels mark possible liquidity areas; actual stops/orders are unavailable.")
    if fib["available"]:
        fib_parts = ", ".join(f"{level['ratio']:.1%}={level['price']:.6g}" for level in fib["levels"])
        nearest_fib = min(fib["levels"], key=lambda level: abs(level["price"] - last["close"]))
        factor(19, "heuristic",
               f"H1 Fibonacci retracement of the latest confirmed {bias.lower()} impulse: {fib_parts}. "
               f"Nearest {nearest_fib['ratio']:.1%} ≈ {nearest_fib['price']:.6g} "
               f"({abs(nearest_fib['price'] - last['close']) / band:.2f} ATR from M15 close). "
               "Heuristic only; not an independent direction vote.")
    else:
        factor(19, "unavailable",
               "No completed H1 swing impulse aligned with the active bias; Fibonacci levels are withheld rather than guessed.")

    candle_events = detect_at(rows, allow_gaps=True)
    charts = chart_patterns(rows)
    breaks = [event for event in breakout_events(rows) if event["epoch"] == last["epoch"]]
    result["events"] = _public_events(candle_events + charts + breaks)
    side = "upper" if bias == "RISE" else "lower"
    candle_confirmation = bias != "WAIT" and any(event["direction"] == side and event["status"] == "confirmed" for event in candle_events)
    factor(10, "confirmed" if candle_confirmation else "neutral", ", ".join(event["type"] for event in candle_events) or "No named rejection/engulfing pattern on the latest closed M15 candle.")
    factor(11, "observed" if charts else "neutral", ", ".join(event["type"] for event in charts) or "No supported M15 double top/bottom, triangle, flag or related chart pattern detected.")

    prior_high = max(row["high"] for row in rows[-21:-1])
    prior_low = min(row["low"] for row in rows[-21:-1])
    body = last["close"] - last["open"]
    span = max(last["high"] - last["low"], 1e-12)
    close_position = (last["close"] - last["low"]) / span
    breakout_up = last["close"] > prior_high + 0.1 * band and body >= 0.4 * band and close_position >= 0.7
    breakout_down = last["close"] < prior_low - 0.1 * band and body <= -0.4 * band and close_position <= 0.3
    setup_breakout = breakout_up if bias == "RISE" else breakout_down if bias == "FALL" else False
    prior_previous_high = max(row["high"] for row in rows[-22:-2])
    prior_previous_low = min(row["low"] for row in rows[-22:-2])
    failed_up = (last["high"] > prior_high and last["close"] < prior_high) or (previous["close"] > prior_previous_high and last["close"] < prior_previous_high)
    failed_down = (last["low"] < prior_low and last["close"] > prior_low) or (previous["close"] < prior_previous_low and last["close"] > prior_previous_low)
    failed_against_bias = (bias == "RISE" and failed_up) or (bias == "FALL" and failed_down)
    if failed_against_bias:
        blockers.append("Latest M15 evidence includes a failed breakout against the proposed direction.")
    if setup_breakout:
        breakout_detail = (
            f"M15 closed beyond the prior 20-bar {'high' if bias == 'RISE' else 'low'} "
            f"({prior_low:.6g}–{prior_high:.6g}) at {last['close']:.6g} with a directional body "
            f"and close in the outer 30% of the bar (≥0.1 ATR clearance).")
    else:
        breakout_detail = (
            f"No M15 breakout through the prior 20-bar range {prior_low:.6g}–{prior_high:.6g}. "
            f"Close {last['close']:.6g}; need 0.1 ATR clearance plus a firm directional body/close.")
    factor(6, "confirmed" if setup_breakout else "neutral", breakout_detail)
    if failed_against_bias:
        false_detail = (
            f"False breakout against {bias}: the M15 wick or prior close pierced the "
            f"{'high' if bias == 'RISE' else 'low'} but price closed back inside the range. Entry blocked.")
    elif failed_up or failed_down:
        false_detail = (
            f"A one-sided failure was observed (upside={failed_up}, downside={failed_down}) "
            f"but it does not oppose the active bias; still noted as caution.")
    else:
        false_detail = "No M15 false-breakout wick or prior-bar reclaim against the active bias on the latest closed candle."
    factor(7, "blocked" if failed_against_bias else "observed" if failed_up or failed_down else "clear", false_detail)

    support_levels = [m15["ema20"]] + [zone["price"] for zone in zones if zone["type"] == ("support" if bias == "RISE" else "resistance")] + [level["price"] for level in fib["levels"]]
    touched_level = any(last["low"] - 0.2 * band <= level <= last["high"] + 0.2 * band for level in support_levels if level is not None)
    recovered = last["close"] > previous["close"] and body > 0 if bias == "RISE" else last["close"] < previous["close"] and body < 0
    setup_pullback = bias != "WAIT" and candle_confirmation and touched_level and recovered and not failed_against_bias
    setup = "range_breakout" if setup_breakout else "pullback_rejection" if setup_pullback else None
    result["setup"] = setup
    if setup is None:
        blockers.append("No credible closed-M15 breakout or level-tested pullback/rejection setup.")
    else:
        reasons.append(f"M15 trigger: {setup.replace('_', ' ')} using the latest closed candle.")

    weak_adx = any((value.get("adx") or 0) < 20 for value in (h1, h4))
    if weak_adx:
        blockers.append("H1/H4 ADX is below 20 or unavailable; trend strength is insufficient.")
    factor(18, "blocked" if weak_adx else "confirmed", f"H1 ADX {(h1.get('adx') or 0):.1f}; H4 ADX {(h4.get('adx') or 0):.1f}; minimum 20. This is a trend-strength heuristic.")
    factor(13, "confirmed" if bias != "WAIT" else "neutral", f"H1 EMA20 {h1['ema20']:.6g}, EMA50 {h1['ema50']:.6g}, SMA20 {h1['sma20']:.6g}. Moving averages share the trend group.")
    momentum_ok = bias != "WAIT" and all((last_ind["rsi"] >= 52 and last_ind["macd"] > last_ind["macd_signal"]) if bias == "RISE" else (last_ind["rsi"] <= 48 and last_ind["macd"] < last_ind["macd_signal"]) for last_ind in (h1, m15))
    factor(8, "confirmed" if momentum_ok else "neutral", "H1 and M15 RSI/MACD align." if momentum_ok else "H1/M15 RSI and MACD do not jointly confirm momentum.")
    factor(14, "observed", f"H1 RSI {h1['rsi']:.1f}; M15 RSI {m15['rsi']:.1f}. Extreme RSI alone is not a reversal signal.")
    factor(15, "observed", f"H1 MACD histogram {h1['macd_hist']:.6g}; M15 {m15['macd_hist']:.6g}. RSI/MACD share one momentum group.")

    atr_history = atr(frame(closed["1h"])).dropna().tolist()
    baseline = median(atr_history[-41:-1]) if len(atr_history) > 1 else 0.0
    atr_ratio = (h1["atr"] / baseline) if baseline > 0 else None
    extreme = atr_ratio is None or atr_ratio > 2.5 or atr_ratio < 0.35 or span > 3 * band or band <= 1e-12
    if extreme:
        blockers.append("ATR regime is unavailable/extreme or the latest M15 bar exceeds 3 ATR.")
    result["volatility"] = {"h1_atr_ratio": atr_ratio, "m15_bar_atr_ratio": span / band, "extreme": extreme}
    factor(9, "blocked" if extreme else "observed", f"H1 ATR versus recent median: {atr_ratio:.2f}x; latest M15 range: {span / band:.2f} ATR." if atr_ratio is not None else "No nonzero ATR baseline.")
    factor(16, "observed", f"H1 ATR14 {h1['atr']:.6g}; H4 ATR14 {h4['atr']:.6g}; M15 ATR14 {m15['atr']:.6g}. Used for scale/volatility gates; a Rise/Fall expiry has no assumed stop loss.")
    bb_width = (m15["bb_upper"] - m15["bb_lower"]) / max(m15["bb_middle"], 1e-12)
    factor(17, "observed", f"M15 bands {m15['bb_lower']:.6g}–{m15['bb_upper']:.6g}; relative width {bb_width:.3%}. Bands describe dispersion, not an independent direction vote.")
    tick_volumes = [row.get("tick_volume") for row in rows[-20:]]
    if all(value is not None for value in tick_volumes):
        factor(20, "observed", f"Latest M15 tick count {tick_volumes[-1]:.0f}; recent median {median(tick_volumes):.0f}. Tick activity is not centralized traded FX volume and does not add a separate score vote.")

    groups = {
        "trend_alignment": {"points": 35 if bias != "WAIT" else 0, "maximum": 35},
        "price_structure": {"points": 20 if structure_match >= 2 else 12 if structure_match == 1 else 5 if bias != "WAIT" and not structure_conflict else 0, "maximum": 20},
        "momentum": {"points": 15 if momentum_ok else 5 if bias != "WAIT" else 0, "maximum": 15},
        "m15_setup": {"points": 20 if setup else 0, "maximum": 20},
        "volatility_regime": {"points": 10 if not extreme and not weak_adx else 0, "maximum": 10},
    }
    if structure_conflict:
        groups["price_structure"]["points"] = 0
    score = sum(group["points"] for group in groups.values())
    result.update(score=score, score_groups=groups)
    if score < 80:
        blockers.append(f"Technical checklist score {score}/100 is below the research threshold of 80.")
    result["ready"] = not blockers
    result["direction"] = bias if result["ready"] else "WAIT"
    return result
