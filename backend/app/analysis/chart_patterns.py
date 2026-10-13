"""Multi-bar chart patterns built from confirmed swings and closed candles."""

from __future__ import annotations

from app.analysis.indicators import last_atr
from app.analysis.structure import swings


def _event(
    name: str,
    direction: str,
    candles: list[dict],
    confidence: float,
    evidence: str,
    lines: list[dict] | None = None,
    **geometry,
) -> dict:
    if lines:
        geometry["lines"] = lines
    return {
        "id": f"{name}:{int(candles[-1]['epoch'])}",
        "type": name,
        "direction": direction,
        "epoch": int(candles[0]["epoch"]),
        "end_epoch": int(candles[-1]["epoch"]),
        "confidence": confidence,
        "status": "confirmed",
        "geometry": geometry,
        "evidence": evidence,
        "invalidation": geometry.get("invalidation"),
    }


def _extend(start: dict, end: dict, last_epoch: int) -> dict:
    """Straight boundary from the first swing through the latest candle."""
    t1 = int(start["epoch"])
    t2 = int(last_epoch)
    p1 = float(start["price"])
    span = int(end["epoch"]) - t1
    p2 = float(end["price"])
    if span:
        p2 = p1 + (p2 - p1) * ((t2 - t1) / span)
    return {"time1": t1, "price1": p1, "time2": max(t2, t1 + 1), "price2": p2}


def chart_patterns(candles: list[dict]) -> list[dict]:
    if len(candles) < 20:
        return []
    points = swings(candles)
    atr = max(last_atr(candles), float(candles[-1]["close"]) * 0.0005)
    events = []
    events.extend(_doubles(points, candles, atr))
    events.extend(_head_shoulders(points, candles, atr))
    events.extend(_contractions(points, candles))
    events.extend(_range_and_flag(candles, atr))
    return events


def _between(points: list[dict], kind: str, left: int, right: int) -> list[dict]:
    return [item for item in points if item["kind"] == kind and left < item["index"] < right]


def _latest_pair(points: list[dict], kind: str, atr: float) -> tuple[dict, dict] | None:
    selected = [item for item in points if item["kind"] == kind]
    for left, right in reversed(list(zip(selected, selected[1:]))):
        if abs(left["price"] - right["price"]) <= atr and right["index"] - left["index"] >= 4:
            return left, right
    return None


def _doubles(points: list[dict], candles: list[dict], atr: float) -> list[dict]:
    events = []
    highs = _latest_pair(points, "high", atr)
    lows = _latest_pair(points, "low", atr)
    if highs:
        a, b = highs
        necks = _between(points, "low", a["index"], b["index"])
        if necks and float(candles[-1]["close"]) < min(item["price"] for item in necks):
            neck = min(item["price"] for item in necks)
            events.append(_event(
                "double_top", "lower", candles, 0.78,
                "Two similar swing highs and a close below the neckline",
                lines=[
                    {"time1": int(a["epoch"]), "price1": float(a["price"]), "time2": int(b["epoch"]), "price2": float(b["price"])},
                    {"time1": int(a["epoch"]), "price1": float(neck), "time2": int(candles[-1]["epoch"]), "price2": float(neck)},
                ],
                neckline=neck, left=a["price"], right=b["price"], invalidation=max(a["price"], b["price"]),
            ))
    if lows:
        a, b = lows
        necks = _between(points, "high", a["index"], b["index"])
        if necks and float(candles[-1]["close"]) > max(item["price"] for item in necks):
            neck = max(item["price"] for item in necks)
            events.append(_event(
                "double_bottom", "upper", candles, 0.78,
                "Two similar swing lows and a close above the neckline",
                lines=[
                    {"time1": int(a["epoch"]), "price1": float(a["price"]), "time2": int(b["epoch"]), "price2": float(b["price"])},
                    {"time1": int(a["epoch"]), "price1": float(neck), "time2": int(candles[-1]["epoch"]), "price2": float(neck)},
                ],
                neckline=neck, left=a["price"], right=b["price"], invalidation=min(a["price"], b["price"]),
            ))
    return events


def _head_shoulders(points: list[dict], candles: list[dict], atr: float) -> list[dict]:
    events = []
    highs = [item for item in points if item["kind"] == "high"]
    lows = [item for item in points if item["kind"] == "low"]
    if len(highs) >= 3:
        left, head, right = highs[-3:]
        shoulders = abs(left["price"] - right["price"]) <= atr
        if shoulders and head["price"] > left["price"] + atr * 0.5 and head["price"] > right["price"] + atr * 0.5:
            necks = _between(points, "low", left["index"], right["index"])
            if len(necks) >= 2 and float(candles[-1]["close"]) < min(item["price"] for item in necks):
                neck = min(item["price"] for item in necks)
                events.append(_event(
                    "head_and_shoulders", "lower", candles, 0.8,
                    "Middle swing high is above two similar shoulders",
                    lines=[
                        {"time1": int(left["epoch"]), "price1": float(left["price"]), "time2": int(head["epoch"]), "price2": float(head["price"])},
                        {"time1": int(head["epoch"]), "price1": float(head["price"]), "time2": int(right["epoch"]), "price2": float(right["price"])},
                        {"time1": int(necks[0]["epoch"]), "price1": float(neck), "time2": int(candles[-1]["epoch"]), "price2": float(neck)},
                    ],
                    neckline=neck, invalidation=head["price"],
                ))
    if len(lows) >= 3:
        left, head, right = lows[-3:]
        shoulders = abs(left["price"] - right["price"]) <= atr
        if shoulders and head["price"] < left["price"] - atr * 0.5 and head["price"] < right["price"] - atr * 0.5:
            necks = _between(points, "high", left["index"], right["index"])
            if len(necks) >= 2 and float(candles[-1]["close"]) > max(item["price"] for item in necks):
                neck = max(item["price"] for item in necks)
                events.append(_event(
                    "inverse_head_and_shoulders", "upper", candles, 0.8,
                    "Middle swing low is below two similar shoulders",
                    lines=[
                        {"time1": int(left["epoch"]), "price1": float(left["price"]), "time2": int(head["epoch"]), "price2": float(head["price"])},
                        {"time1": int(head["epoch"]), "price1": float(head["price"]), "time2": int(right["epoch"]), "price2": float(right["price"])},
                        {"time1": int(necks[0]["epoch"]), "price1": float(neck), "time2": int(candles[-1]["epoch"]), "price2": float(neck)},
                    ],
                    neckline=neck, invalidation=head["price"],
                ))
    return events


def _contractions(points: list[dict], candles: list[dict]) -> list[dict]:
    highs = [item for item in points if item["kind"] == "high"][-3:]
    lows = [item for item in points if item["kind"] == "low"][-3:]
    if len(highs) < 3 or len(lows) < 3:
        return []
    high_slope = highs[-1]["price"] - highs[0]["price"]
    low_slope = lows[-1]["price"] - lows[0]["price"]
    last = int(candles[-1]["epoch"])
    bounds = [_extend(highs[0], highs[-1], last), _extend(lows[0], lows[-1], last)]
    events = []
    if high_slope < 0 and low_slope > 0:
        events.append(_event(
            "triangle", "neutral", candles, 0.72,
            "Highs are falling while lows are rising",
            lines=bounds,
            high1=highs[0]["price"], high2=highs[-1]["price"], low1=lows[0]["price"], low2=lows[-1]["price"],
        ))
    elif high_slope < 0 and low_slope < 0 and abs(high_slope) > abs(low_slope):
        events.append(_event("falling_wedge", "upper", candles, 0.7, "Both boundaries fall and converge", lines=bounds, high_slope=high_slope, low_slope=low_slope))
    elif high_slope > 0 and low_slope > 0 and abs(low_slope) > abs(high_slope):
        events.append(_event("rising_wedge", "lower", candles, 0.7, "Both boundaries rise and converge", lines=bounds, high_slope=high_slope, low_slope=low_slope))
    elif high_slope > 0 and low_slope > 0:
        events.append(_event("rising_channel", "upper", candles, 0.66, "Swing highs and lows are both rising", lines=bounds, high_slope=high_slope, low_slope=low_slope))
    elif high_slope < 0 and low_slope < 0:
        events.append(_event("falling_channel", "lower", candles, 0.66, "Swing highs and lows are both falling", lines=bounds, high_slope=high_slope, low_slope=low_slope))
    return events


def _range_and_flag(candles: list[dict], atr: float) -> list[dict]:
    events = []
    window = candles[-20:]
    hi = max(float(item["high"]) for item in window[:-1])
    lo = min(float(item["low"]) for item in window[:-1])
    close = float(candles[-1]["close"])
    if hi - lo <= atr * 3:
        if close > hi:
            events.append(_event(
                "range_breakout", "upper", candles[-20:], 0.76, "Close above a tight 20-bar range",
                lines=[{"time1": int(window[0]["epoch"]), "price1": hi, "time2": int(candles[-1]["epoch"]), "price2": hi}],
                level=hi, invalidation=lo,
            ))
        elif close < lo:
            events.append(_event(
                "range_breakout", "lower", candles[-20:], 0.76, "Close below a tight 20-bar range",
                lines=[{"time1": int(window[0]["epoch"]), "price1": lo, "time2": int(candles[-1]["epoch"]), "price2": lo}],
                level=lo, invalidation=hi,
            ))
    impulse = candles[-8:-4]
    flag = candles[-4:-1]
    if len(impulse) == 4 and len(flag) == 3:
        move = float(impulse[-1]["close"]) - float(impulse[0]["open"])
        flag_range = max(float(item["high"]) for item in flag) - min(float(item["low"]) for item in flag)
        if move > atr * 1.5 and flag_range < abs(move) * 0.5 and close > max(float(item["high"]) for item in flag):
            events.append(_event(
                "bull_flag", "upper", candles[-8:], 0.7,
                "Strong advance followed by a tight pause and upside close",
                lines=[
                    {"time1": int(flag[0]["epoch"]), "price1": float(flag[0]["high"]), "time2": int(flag[-1]["epoch"]), "price2": float(flag[-1]["high"])},
                    {"time1": int(flag[0]["epoch"]), "price1": float(flag[0]["low"]), "time2": int(flag[-1]["epoch"]), "price2": float(flag[-1]["low"])},
                ],
                invalidation=min(float(item["low"]) for item in flag),
            ))
        if move < -atr * 1.5 and flag_range < abs(move) * 0.5 and close < min(float(item["low"]) for item in flag):
            events.append(_event(
                "bear_flag", "lower", candles[-8:], 0.7,
                "Strong decline followed by a tight pause and downside close",
                lines=[
                    {"time1": int(flag[0]["epoch"]), "price1": float(flag[0]["high"]), "time2": int(flag[-1]["epoch"]), "price2": float(flag[-1]["high"])},
                    {"time1": int(flag[0]["epoch"]), "price1": float(flag[0]["low"]), "time2": int(flag[-1]["epoch"]), "price2": float(flag[-1]["low"])},
                ],
                invalidation=max(float(item["high"]) for item in flag),
            ))
    return events
