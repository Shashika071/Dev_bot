"""Swing structure, support/resistance, and trendlines."""

from __future__ import annotations

import numpy as np

from app.analysis.indicators import last_atr


def swings(candles: list[dict], left: int = 3, right: int = 3) -> list[dict]:
    """Confirmed pivots. The right-hand bars must already be closed."""
    found = []
    if len(candles) < left + right + 1:
        return found
    for index in range(left, len(candles) - right):
        window = candles[index - left:index + right + 1]
        high = float(candles[index]["high"])
        low = float(candles[index]["low"])
        if high >= max(float(item["high"]) for item in window):
            found.append({
                "kind": "high",
                "index": index,
                "epoch": int(candles[index]["epoch"]),
                "price": high,
            })
        if low <= min(float(item["low"]) for item in window):
            found.append({
                "kind": "low",
                "index": index,
                "epoch": int(candles[index]["epoch"]),
                "price": low,
            })
    return found


def structure_state(points: list[dict]) -> str:
    highs = [item for item in points if item["kind"] == "high"][-2:]
    lows = [item for item in points if item["kind"] == "low"][-2:]
    if len(highs) < 2 or len(lows) < 2:
        return "insufficient"
    higher_high = highs[-1]["price"] > highs[-2]["price"]
    higher_low = lows[-1]["price"] > lows[-2]["price"]
    lower_high = highs[-1]["price"] < highs[-2]["price"]
    lower_low = lows[-1]["price"] < lows[-2]["price"]
    if higher_high and higher_low:
        return "HH_HL"
    if lower_high and lower_low:
        return "LH_LL"
    if higher_high and lower_low:
        return "expanding"
    return "mixed"


def support_resistance(candles: list[dict], points: list[dict] | None = None) -> list[dict]:
    points = swings(candles) if points is None else points
    if not points:
        return []
    tolerance = max(last_atr(candles) * 0.5, float(candles[-1]["close"]) * 0.0005)
    zones = []
    used: set[int] = set()
    ordered = sorted(enumerate(points), key=lambda item: item[1]["price"])
    for position, (index, point) in enumerate(ordered):
        if index in used:
            continue
        cluster = [point]
        used.add(index)
        for other_pos, (other_index, other) in enumerate(ordered[position + 1:], start=position + 1):
            del other_pos
            if other_index in used:
                continue
            if abs(other["price"] - point["price"]) <= tolerance and other["kind"] == point["kind"]:
                cluster.append(other)
                used.add(other_index)
        if len(cluster) < 2:
            continue
        price = float(np.mean([item["price"] for item in cluster]))
        zones.append({
            "type": "resistance" if point["kind"] == "high" else "support",
            "direction": "lower" if point["kind"] == "high" else "upper",
            "price": price,
            "low": price - tolerance,
            "high": price + tolerance,
            "touches": len(cluster),
            "epoch": int(min(item["epoch"] for item in cluster)),
            "end_epoch": int(max(item["epoch"] for item in cluster)),
            "confidence": min(0.9, 0.45 + 0.1 * len(cluster)),
            "status": "active",
            "evidence": f"{len(cluster)} swing {point['kind']}s clustered within {tolerance:.4f}",
        })
    return zones


def _line_fit(window: list[dict]) -> tuple[float, float]:
    x = np.array([float(item["epoch"]) for item in window], dtype=float)
    y = np.array([float(item["price"]) for item in window], dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    return float(slope), float(intercept)


def trendlines(candles: list[dict], points: list[dict] | None = None) -> list[dict]:
    """Upper and lower boundaries through recent swings, extended to the latest candle."""
    points = swings(candles) if points is None else points
    if len(candles) < 2:
        return []
    end_epoch = int(candles[-1]["epoch"])
    lines = []
    for kind, name, direction in (("low", "support_trendline", "upper"), ("high", "resistance_trendline", "lower")):
        selected = [item for item in points if item["kind"] == kind]
        if len(selected) < 2:
            continue
        window = selected[-6:]
        slope, intercept = _line_fit(window)
        if len(window) >= 3:
            x = np.array([float(item["epoch"]) for item in window], dtype=float)
            y = np.array([float(item["price"]) for item in window], dtype=float)
            residual = float(np.max(np.abs(slope * x + intercept - y)))
            tolerance = max(last_atr(candles) * 0.75, 1e-6)
            if residual > tolerance:
                window = selected[-2:]
                slope, intercept = _line_fit(window)
        start = window[0]
        lines.append({
            "id": f"{name}:{int(start['epoch'])}:{end_epoch}",
            "type": name,
            "direction": direction,
            "confidence": 0.7,
            "status": "active",
            "epoch": int(start["epoch"]),
            "end_epoch": end_epoch,
            "geometry": {
                "time1": int(start["epoch"]),
                "price1": float(slope * float(start["epoch"]) + intercept),
                "time2": end_epoch,
                "price2": float(slope * end_epoch + intercept),
            },
            "touches": len(window),
            "evidence": f"{len(window)} swing {kind}s form the channel line",
        })
    return lines
