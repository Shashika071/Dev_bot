"""Chart-only Binary Campus trend confirmation.

Independent Python reading of the MPL-2.0 "Binary Campus - Trend Confirmation
System" rules: swing structure, ROC versus zero, and TSI versus its signal.
Default mode is "Exact Same Candle". This does not place trades.
"""
from __future__ import annotations

import math

from app.synthetic.indicators import ema

MS_LENGTH = 5
TSI_SHORT = 5
TSI_LONG = 20
TSI_SIGNAL = 13
ROC_LENGTH = 9


def _roc(closes: list[float], length: int) -> list[float | None]:
    result: list[float | None] = [None] * len(closes)
    for index in range(length, len(closes)):
        base = closes[index - length]
        if base > 0 and math.isfinite(base) and math.isfinite(closes[index]):
            result[index] = 100.0 * (closes[index] - base) / base
    return result


def _tsi(closes: list[float], short: int, long: int) -> list[float]:
    change = [0.0] + [right - left for left, right in zip(closes, closes[1:])]
    numerator = ema(ema(change, long), short)
    denominator = ema(ema([abs(value) for value in change], long), short)
    result = []
    for num, den in zip(numerator, denominator):
        result.append(0.0 if den == 0 else 100.0 * num / den)
    return result


def _crossed_over(current: float | None, previous: float | None, level: float = 0.0) -> bool:
    return current is not None and previous is not None and previous <= level < current


def _crossed_under(current: float | None, previous: float | None, level: float = 0.0) -> bool:
    return current is not None and previous is not None and previous >= level > current


def _pivot(candles: list[dict], index: int, length: int, key: str, higher: bool) -> float | None:
    pivot_index = index - length
    if pivot_index < length:
        return None
    window = candles[pivot_index - length:pivot_index + length + 1]
    value = float(candles[pivot_index][key])
    others = [float(row[key]) for offset, row in enumerate(window) if pivot_index - length + offset != pivot_index]
    if higher and all(value > other for other in others):
        return value
    if not higher and all(value < other for other in others):
        return value
    return None


def trend_confirmation(candles: list[dict]) -> dict:
    """BUY/SELL marks plus the latest structure, ROC, and TSI state."""
    closes = [float(row["close"]) for row in candles]
    roc = _roc(closes, ROC_LENGTH)
    tsi = _tsi(closes, TSI_SHORT, TSI_LONG) if closes else []
    signal = ema(tsi, TSI_SIGNAL) if tsi else []
    trend = 0
    wait_bull = False
    wait_bear = False
    last_high: float | None = None
    last_low: float | None = None
    marks = []
    for index, row in enumerate(candles):
        pivot_high = _pivot(candles, index, MS_LENGTH, "high", True)
        pivot_low = _pivot(candles, index, MS_LENGTH, "low", False)
        if pivot_high is not None:
            last_high = pivot_high
            wait_bull = True
        if pivot_low is not None:
            last_low = pivot_low
            wait_bear = True
        close = closes[index]
        previous_close = closes[index - 1] if index else None
        if previous_close is not None and last_high is not None and wait_bull and previous_close <= last_high < close:
            trend = 1
            wait_bull = False
        if previous_close is not None and last_low is not None and wait_bear and previous_close >= last_low > close:
            trend = -1
            wait_bear = False
        roc_up = index > 0 and _crossed_over(roc[index], roc[index - 1])
        roc_down = index > 0 and _crossed_under(roc[index], roc[index - 1])
        tsi_up = index > 0 and signal and _crossed_over(tsi[index] - signal[index], tsi[index - 1] - signal[index - 1])
        tsi_down = index > 0 and signal and _crossed_under(tsi[index] - signal[index], tsi[index - 1] - signal[index - 1])
        if trend == 1 and roc_up and tsi_up:
            marks.append({"epoch": int(row["epoch"]), "side": "BUY"})
        elif trend == -1 and roc_down and tsi_down:
            marks.append({"epoch": int(row["epoch"]), "side": "SELL"})
    last_roc = next((value for value in reversed(roc) if value is not None), None)
    last_tsi = tsi[-1] if tsi else None
    last_signal = signal[-1] if signal else None
    if trend > 0:
        trend_text = "BULLISH (UP)"
    elif trend < 0:
        trend_text = "BEARISH (DOWN)"
    else:
        trend_text = "NEUTRAL"
    roc_text = "Above 0 (Bullish)" if last_roc is not None and last_roc > 0 else "Below 0 (Bearish)"
    if last_tsi is None or last_signal is None:
        tsi_text = "Waiting"
    elif last_tsi > last_signal:
        tsi_text = "TSI > Signal (Bullish)"
    else:
        tsi_text = "TSI < Signal (Bearish)"
    return {
        "signals": marks,
        "dashboard": {
            "trend": trend_text,
            "roc": roc_text,
            "tsi": tsi_text,
            "mode": "Exact Same Candle",
        },
        "attribution": "Binary Campus Trend Confirmation System (MPL-2.0). Chart display only.",
    }
