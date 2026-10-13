"""Independent strategy readings and an explainable confluence summary."""

from __future__ import annotations


def _reading(name: str, direction: str, score: float, evidence: list[str], invalidation: float | None) -> dict:
    return {
        "name": name,
        "direction": direction,
        "score": round(score, 2),
        "evidence": evidence,
        "invalidation": invalidation,
    }


def evaluate_strategies(
    *,
    regime: str,
    indicators: dict,
    events: list[dict],
) -> list[dict]:
    last = indicators or {}
    readings = []
    rsi_value = last.get("rsi")
    hist = last.get("macd_hist")
    ema8 = last.get("ema8")
    ema21 = last.get("ema21")
    ema20 = last.get("ema20")
    ema50 = last.get("ema50")
    adx = last.get("adx")
    close = last.get("close")
    atr = last.get("atr") or 0
    recent = events[-8:]

    if regime == "trend_up" and ema20 and ema50 and ema20 > ema50:
        readings.append(_reading(
            "trend_following", "upper", 62 + min(float(adx or 0), 20),
            ["EMA 20 is above EMA 50"],
            None if close is None else float(close) - float(atr),
        ))
    elif regime == "trend_up" and ema8 and ema21 and ema8 > ema21:
        readings.append(_reading(
            "trend_following", "upper", 58,
            ["EMA 8 is above EMA 21"],
            None if close is None else float(close) - float(atr),
        ))
    if regime == "trend_down" and ema20 and ema50 and ema20 < ema50:
        readings.append(_reading(
            "trend_following", "lower", 62 + min(float(adx or 0), 20),
            ["EMA 20 is below EMA 50"],
            None if close is None else float(close) + float(atr),
        ))
    elif regime == "trend_down" and ema8 and ema21 and ema8 < ema21:
        readings.append(_reading(
            "trend_following", "lower", 58,
            ["EMA 8 is below EMA 21"],
            None if close is None else float(close) + float(atr),
        ))

    for event in recent:
        if event["type"] in {"donchian_breakout", "range_breakout", "breakout_retest"} and event["direction"] in {"upper", "lower"}:
            readings.append(_reading(
                "breakout_retest", event["direction"], 68 if event["type"] == "breakout_retest" else 60,
                [event.get("evidence") or event["type"]],
                event.get("invalidation"),
            ))
            break

    if rsi_value is not None and hist is not None:
        if 55 <= float(rsi_value) <= 72 and float(hist) > 0:
            readings.append(_reading("momentum", "upper", 58, [f"RSI {float(rsi_value):.1f} with positive MACD"], None))
        if 28 <= float(rsi_value) <= 45 and float(hist) < 0:
            readings.append(_reading("momentum", "lower", 58, [f"RSI {float(rsi_value):.1f} with negative MACD"], None))

    if regime == "range" and rsi_value is not None:
        if float(rsi_value) >= 75:
            readings.append(_reading("mean_reversion", "lower", 57, [f"Range regime and RSI {float(rsi_value):.1f}"], last.get("bb_upper")))
        elif float(rsi_value) <= 25:
            readings.append(_reading("mean_reversion", "upper", 57, [f"Range regime and RSI {float(rsi_value):.1f}"], last.get("bb_lower")))
    return readings


def fuse(readings: list[dict], *, fresh: bool, atr: float, close: float | None) -> dict:
    upper = [item for item in readings if item["direction"] == "upper"]
    lower = [item for item in readings if item["direction"] == "lower"]
    upper_score = max((item["score"] for item in upper), default=0)
    lower_score = max((item["score"] for item in lower), default=0)
    conflicts = []
    if upper and lower:
        conflicts.append("Strategies disagree on direction")
    if not fresh:
        conflicts.append("Market data is stale")
    if conflicts or abs(upper_score - lower_score) < 8:
        direction = "neutral"
        score = 0
        chosen = []
    elif upper_score > lower_score:
        direction = "upper"
        score = upper_score
        chosen = upper
    else:
        direction = "lower"
        score = lower_score
        chosen = lower
    zone = None
    invalidation = None
    if close is not None and direction != "neutral":
        pad = max(float(atr), abs(close) * 0.0003)
        zone = [round(close - pad * 0.25, 5), round(close + pad * 0.25, 5)]
        levels = [item["invalidation"] for item in chosen if item.get("invalidation") is not None]
        if levels:
            invalidation = float(levels[0])
    return {
        "direction": direction,
        "ta_score": round(float(score), 2),
        "evidence": [text for item in chosen for text in item["evidence"]],
        "conflicts": conflicts,
        "entry_zone": zone,
        "invalidation": invalidation,
        "fresh": fresh,
        "strategies": [item["name"] for item in chosen],
    }
