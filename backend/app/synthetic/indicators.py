"""Causal indicators for the Volatility 100 one-minute strategy.

This is an independent Python adaptation, not a verified Pine-script port.
FRAMA uses HL2, Ehlers' fractal alpha clipped to [0.01, 1], a five-bar SMA
of its recursive value and bands of 1.5 times the 200-bar mean high-low range.
Range Filter uses close, EMA(EMA(abs(change), 100), 199) * 3 and a step line.
Its trigger is a price/line cross or a change in the confirmed filter direction.

Author descriptions, consulted 2026-10-20:
https://www.tradingview.com/script/hskyfPWP-FRAMA-Channel-BigBeluga/
https://www.tradingview.com/script/J8GzFGfD-Range-Filter-Buy-and-Sell-5min-guikroth-version/
Exact initialization, source choice and arrow timing can differ from TradingView.
"""

from __future__ import annotations

import math
from collections import deque


def _series_step(candles: list[dict]) -> int:
    if len(candles) < 2:
        return 60
    gap = int(candles[1]["epoch"]) - int(candles[0]["epoch"])
    return gap if gap > 0 else 60


def ema(values: list[float], period: int) -> list[float]:
    if period < 1:
        raise ValueError("EMA period must be positive")
    result: list[float] = []
    weight = 2.0 / (period + 1)
    for value in values:
        result.append(value if not result else weight * value + (1 - weight) * result[-1])
    return result


def sma(values: list[float], period: int) -> list[float | None]:
    if period < 1:
        raise ValueError("SMA period must be positive")
    queue: deque[float] = deque()
    total = 0.0
    result = []
    for value in values:
        queue.append(value)
        total += value
        if len(queue) > period:
            total -= queue.popleft()
        result.append(total / period if len(queue) == period else None)
    return result


def wilder(values: list[float], period: int) -> list[float]:
    result: list[float] = []
    for index, value in enumerate(values):
        if not result:
            result.append(value)
        elif index < period:
            result.append((result[-1] * index + value) / (index + 1))
        else:
            result.append((result[-1] * (period - 1) + value) / period)
    return result


def technical_series(candles: list[dict]) -> dict[str, list[float]]:
    closes = [float(row["close"]) for row in candles]
    if not closes:
        return {key: [] for key in ("ema20", "ema50", "rsi", "atr", "macd", "macd_signal", "macd_hist")}
    changes = [0.0] + [right - left for left, right in zip(closes, closes[1:])]
    gains = wilder([max(0.0, value) for value in changes], 14)
    losses = wilder([max(0.0, -value) for value in changes], 14)
    rsi = [
        50.0 if gain == loss == 0 else 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)
        for gain, loss in zip(gains, losses)
    ]
    true_ranges = [
        max(float(row["high"]) - float(row["low"]),
            abs(float(row["high"]) - closes[max(0, index - 1)]),
            abs(float(row["low"]) - closes[max(0, index - 1)]))
        for index, row in enumerate(candles)
    ]
    fast, slow = ema(closes, 12), ema(closes, 26)
    macd = [a - b for a, b in zip(fast, slow)]
    signal = ema(macd, 9)
    return {
        "ema20": ema(closes, 20), "ema50": ema(closes, 50), "rsi": rsi,
        "atr": wilder(true_ranges, 14), "macd": macd, "macd_signal": signal,
        "macd_hist": [a - b for a, b in zip(macd, signal)],
    }


def frama_channel(
    candles: list[dict], length: int = 26, band_multiplier: float = 1.5,
    range_period: int = 200, smoothing: int = 5,
) -> list[dict]:
    if length < 4 or length % 2:
        raise ValueError("FRAMA length must be an even number >= 4")
    if range_period < 1 or smoothing < 1 or band_multiplier <= 0:
        raise ValueError("FRAMA range, smoothing and band multiplier must be positive")
    half = length // 2
    raw, alphas, dimensions = [], [], []
    for index, row in enumerate(candles):
        price = (float(row["high"]) + float(row["low"])) / 2
        dimension = 1.0
        if index >= length - 1:
            window = candles[index - length + 1:index + 1]
            first, second = window[:half], window[half:]
            n1 = (max(item["high"] for item in first) - min(item["low"] for item in first)) / half
            n2 = (max(item["high"] for item in second) - min(item["low"] for item in second)) / half
            n3 = (max(item["high"] for item in window) - min(item["low"] for item in window)) / length
            if min(n1, n2, n3) > 0:
                dimension = (math.log(n1 + n2) - math.log(n3)) / math.log(2)
        alpha = max(0.01, min(1.0, math.exp(-4.6 * (dimension - 1))))
        raw.append(price if not raw else alpha * price + (1 - alpha) * raw[-1])
        alphas.append(alpha)
        dimensions.append(dimension)
    midpoints = sma(raw, smoothing)
    ranges = sma([float(row["high"]) - float(row["low"]) for row in candles], range_period)
    step = _series_step(candles)
    result = []
    last_signal: dict[str, int | None] = {"RISE": None, "FALL": None}
    for index, (row, midpoint, spread) in enumerate(zip(candles, midpoints, ranges)):
        upper = None if midpoint is None or spread is None else midpoint + band_multiplier * spread
        lower = None if midpoint is None or spread is None else midpoint - band_multiplier * spread
        close = float(row["close"])
        direction = "NEUTRAL" if upper is None else "RISE" if close > upper else "FALL" if close < lower else "NEUTRAL"
        cross = "NEUTRAL"
        if index and upper is not None and result[-1]["upper"] is not None:
            previous_close = float(candles[index - 1]["close"])
            if close > upper and previous_close <= result[-1]["upper"]:
                cross = "RISE"
            elif close < lower and previous_close >= result[-1]["lower"]:
                cross = "FALL"
        if cross != "NEUTRAL":
            last_signal[cross] = index
        signal_index = last_signal.get(direction)
        result.append({
            "epoch": int(row["epoch"]), "mid": midpoint, "upper": upper, "lower": lower,
            "alpha": alphas[index], "dimension": dimensions[index], "direction": direction,
            "cross": cross, "signal_age": None if signal_index is None else index - signal_index,
            "signal_epoch": None if signal_index is None else int(candles[signal_index]["epoch"]) + step,
        })
    return result


def range_filter(candles: list[dict], period: int = 100, multiplier: float = 3.0) -> list[dict]:
    if period < 1 or multiplier <= 0:
        raise ValueError("Range Filter period and multiplier must be positive")
    closes = [float(row["close"]) for row in candles]
    changes = [0.0] + [abs(right - left) for left, right in zip(closes, closes[1:])] if closes else []
    spreads = [value * multiplier for value in ema(ema(changes, period), 2 * period - 1)]
    result = []
    step = _series_step(candles)
    slope = 0
    previous_confirmed = "NEUTRAL"
    last_signal: dict[str, int | None] = {"RISE": None, "FALL": None}
    for index, (row, close, spread) in enumerate(zip(candles, closes, spreads)):
        previous = result[-1]["line"] if result else close
        line = max(previous, close - spread) if close > previous else min(previous, close + spread)
        slope = 1 if line > previous else -1 if line < previous else slope
        direction = "RISE" if close > line and slope > 0 else "FALL" if close < line and slope < 0 else "NEUTRAL"
        cross = "NEUTRAL"
        if index and direction != "NEUTRAL":
            price_cross = (direction == "RISE" and closes[index - 1] <= previous) or (
                direction == "FALL" and closes[index - 1] >= previous)
            if direction != previous_confirmed or price_cross:
                cross = direction
                last_signal[cross] = index
            previous_confirmed = direction
        signal_index = last_signal.get(direction)
        result.append({
            "epoch": int(row["epoch"]), "line": line, "range": spread,
            "direction": direction, "cross": cross, "slope": slope,
            "signal_age": None if signal_index is None else index - signal_index,
            "signal_epoch": None if signal_index is None else int(candles[signal_index]["epoch"]) + step,
        })
    return result
