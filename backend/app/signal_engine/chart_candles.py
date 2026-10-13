"""Aggregate tick quotes into OHLC candles for the dashboard chart."""

from __future__ import annotations

from typing import Iterable


INTERVAL_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "1d": 86400,
}


def select_older_candles(rows: list[dict], before: int) -> tuple[list[dict], bool]:
    """Keep candles strictly older than `before`.

    A page whose newest bar is still at or after `before` did not move backward
    (the history API wrapped to the latest candles). That page is exhausted.
    """
    parsed: list[dict] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        try:
            epoch = int(row["epoch"])
        except (KeyError, TypeError, ValueError):
            continue
        parsed.append({**row, "epoch": epoch})
    if not parsed or max(int(row["epoch"]) for row in parsed) >= int(before):
        return [], True
    parsed.sort(key=lambda row: int(row["epoch"]))
    deduped: list[dict] = []
    for row in parsed:
        if deduped and int(deduped[-1]["epoch"]) == int(row["epoch"]):
            deduped[-1] = row
        else:
            deduped.append(row)
    return deduped, False


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
