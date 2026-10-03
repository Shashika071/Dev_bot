"""Aggregate tick quotes into OHLC candles for the dashboard chart."""

from __future__ import annotations

from typing import Iterable


INTERVAL_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
}


def aggregate_ohlc(
    ticks: Iterable[tuple[int, float]],
    interval: str,
    max_candles: int = 120,
) -> list[dict]:
    """
    Build OHLC candles from (epoch_seconds, quote) pairs.
    Incomplete current candle is included.
    """
    step = INTERVAL_SECONDS.get(interval)
    if not step:
        raise ValueError(f"Unsupported interval: {interval}")

    buckets: dict[int, dict] = {}
    for epoch, quote in ticks:
        e = int(epoch)
        q = float(quote)
        bucket = (e // step) * step
        c = buckets.get(bucket)
        if c is None:
            buckets[bucket] = {
                "epoch": bucket,
                "open": q,
                "high": q,
                "low": q,
                "close": q,
                "ticks": 1,
            }
        else:
            c["high"] = max(c["high"], q)
            c["low"] = min(c["low"], q)
            c["close"] = q
            c["ticks"] += 1

    candles = [buckets[k] for k in sorted(buckets.keys())]
    if max_candles > 0 and len(candles) > max_candles:
        candles = candles[-max_candles:]
    return candles
