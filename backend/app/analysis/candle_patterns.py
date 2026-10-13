"""Closed-bar candle pattern detections.

Gap patterns are marked unavailable for continuous synthetic indices such as R_100.
"""

from __future__ import annotations


def _num(candle: dict) -> tuple[float, float, float, float]:
    return (
        float(candle["open"]),
        float(candle["high"]),
        float(candle["low"]),
        float(candle["close"]),
    )


def _body(o: float, c: float) -> float:
    return abs(c - o)


def _range(h: float, l: float) -> float:
    return max(h - l, 1e-12)


def _event(name: str, direction: str, candle: dict, confidence: float, evidence: str) -> dict:
    return {
        "type": name,
        "direction": direction,
        "timeframe": candle.get("interval"),
        "epoch": int(candle["epoch"]),
        "end_epoch": int(candle["epoch"]),
        "confidence": confidence,
        "status": "confirmed",
        "geometry": {"price": float(candle["close"])},
        "evidence": evidence,
        "invalidation": float(candle["low"] if direction == "upper" else candle["high"]),
    }


def detect_at(candles: list[dict], *, allow_gaps: bool = False) -> list[dict]:
    """Patterns completed by the last candle. Earlier candles are context only."""
    if len(candles) < 2:
        return []
    events: list[dict] = []
    o0, h0, l0, c0 = _num(candles[-1])
    o1, h1, l1, c1 = _num(candles[-2])
    rng0 = _range(h0, l0)
    body0 = _body(o0, c0)
    body1 = _body(o1, c1)
    upper0 = h0 - max(o0, c0)
    lower0 = min(o0, c0) - l0
    bull0, bear0 = c0 > o0, c0 < o0
    bull1, bear1 = c1 > o1, c1 < o1
    doji0 = body0 / rng0 <= 0.12

    if body0 / rng0 >= 0.7 and upper0 <= 0.1 * rng0 and lower0 <= 0.1 * rng0:
        if bull0:
            events.append(_event("bullish_marubozu", "upper", candles[-1], 0.72, "Wide bullish body with tiny wicks"))
        elif bear0:
            events.append(_event("bearish_marubozu", "lower", candles[-1], 0.72, "Wide bearish body with tiny wicks"))
    if lower0 >= 2.0 * max(body0, 1e-12) and upper0 <= body0 and bull0:
        events.append(_event("hammer", "upper", candles[-1], 0.7, "Long lower wick and small body"))
    if upper0 >= 2.0 * max(body0, 1e-12) and lower0 <= body0 and bear0:
        events.append(_event("shooting_star", "lower", candles[-1], 0.7, "Long upper wick and small body"))
    if doji0 and lower0 >= 2.5 * max(body0, 1e-12) and upper0 <= body0:
        events.append(_event("dragonfly_doji", "upper", candles[-1], 0.66, "Doji with a long lower wick"))
    if doji0 and upper0 >= 2.5 * max(body0, 1e-12) and lower0 <= body0:
        events.append(_event("gravestone_doji", "lower", candles[-1], 0.66, "Doji with a long upper wick"))
    if bull0 and lower0 <= 0.08 * rng0 and body0 / rng0 >= 0.55:
        events.append(_event("bullish_belt_hold", "upper", candles[-1], 0.64, "Bullish open near the low"))
    if bear0 and upper0 <= 0.08 * rng0 and body0 / rng0 >= 0.55:
        events.append(_event("bearish_belt_hold", "lower", candles[-1], 0.64, "Bearish open near the high"))

    if bear1 and bull0 and c0 >= o1 and o0 <= c1 and body0 > body1:
        events.append(_event("bullish_engulfing", "upper", candles[-1], 0.8, "Bullish body engulfs the prior bearish body"))
    if bull1 and bear0 and c0 <= o1 and o0 >= c1 and body0 > body1:
        events.append(_event("bearish_engulfing", "lower", candles[-1], 0.8, "Bearish body engulfs the prior bullish body"))
    if bear1 and bull0 and max(o0, c0) < max(o1, c1) and min(o0, c0) > min(o1, c1) and body0 < body1 * 0.7:
        events.append(_event("bullish_harami", "upper", candles[-1], 0.62, "Small bullish body inside the prior bearish body"))
    if bull1 and bear0 and max(o0, c0) < max(o1, c1) and min(o0, c0) > min(o1, c1) and body0 < body1 * 0.7:
        events.append(_event("bearish_harami", "lower", candles[-1], 0.62, "Small bearish body inside the prior bullish body"))
    mid1 = (o1 + c1) / 2
    if bear1 and bull0 and o0 < l1 and c0 > mid1 and c0 < o1:
        events.append(_event("piercing_line", "upper", candles[-1], 0.74, "Bullish close past the midpoint of the prior body"))
    if bull1 and bear0 and o0 > h1 and c0 < mid1 and c0 > o1:
        events.append(_event("dark_cloud_cover", "lower", candles[-1], 0.74, "Bearish close past the midpoint of the prior body"))
    if abs(l0 - l1) <= 0.15 * max(rng0, _range(h1, l1)) and bear1 and bull0:
        events.append(_event("tweezer_bottom", "upper", candles[-1], 0.6, "Matching lows with a bullish reversal"))
    if abs(h0 - h1) <= 0.15 * max(rng0, _range(h1, l1)) and bull1 and bear0:
        events.append(_event("tweezer_top", "lower", candles[-1], 0.6, "Matching highs with a bearish reversal"))

    if len(candles) >= 3:
        o2, h2, l2, c2 = _num(candles[-3])
        body2 = _body(o2, c2)
        bull2, bear2 = c2 > o2, c2 < o2
        doji1 = body1 / _range(h1, l1) <= 0.12
        small_mid = body1 <= 0.35 * max(body2, body0, 1e-12) or doji1
        if h1 <= h2 and l1 >= l2 and c0 > h1:
            events.append(_event("inside_bar_upside_break", "upper", candles[-1], 0.68, "Inside bar followed by an upside close"))
        if h1 <= h2 and l1 >= l2 and c0 < l1:
            events.append(_event("inside_bar_downside_break", "lower", candles[-1], 0.68, "Inside bar followed by a downside close"))
        if bear2 and small_mid and bull0 and c0 > mid1 and c0 > (o2 + c2) / 2:
            events.append(_event("morning_star", "upper", candles[-1], 0.84, "Bearish candle, small middle, bullish recovery"))
        if bull2 and small_mid and bear0 and c0 < mid1 and c0 < (o2 + c2) / 2:
            events.append(_event("evening_star", "lower", candles[-1], 0.84, "Bullish candle, small middle, bearish reversal"))
        if bull2 and bull1 and bull0 and c0 > c1 > c2 and o0 > o1 > o2:
            events.append(_event("three_white_soldiers", "upper", candles[-1], 0.82, "Three rising bullish closes"))
        if bear2 and bear1 and bear0 and c0 < c1 < c2 and o0 < o1 < o2:
            events.append(_event("three_black_crows", "lower", candles[-1], 0.82, "Three falling bearish closes"))
        if allow_gaps and bear2 and doji1 and h1 < l2 and bull0 and l0 > h1:
            events.append(_event("bullish_abandoned_baby", "upper", candles[-1], 0.4, "Gap doji; down-weighted on continuous synthetics"))
        elif not allow_gaps and bear2 and doji1 and h1 < l2 and bull0 and l0 > h1:
            events.append({
                **_event("bullish_abandoned_baby", "upper", candles[-1], 0.2, "Gap pattern is not relied on for this continuous market"),
                "status": "downweighted",
            })

    for item in events:
        item["id"] = f"{item['type']}:{item['epoch']}"
    return events


def detect_recent(candles: list[dict], *, lookback: int = 40, allow_gaps: bool = False) -> list[dict]:
    events = []
    start = max(2, len(candles) - lookback)
    for index in range(start, len(candles) + 1):
        events.extend(detect_at(candles[:index], allow_gaps=allow_gaps))
    dedup = {}
    for event in events:
        dedup[event["id"]] = event
    return list(dedup.values())
