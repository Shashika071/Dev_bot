"""Turn public R_100 ticks into closed one-minute candles. A gapped minute is skipped."""
from __future__ import annotations

from pathlib import Path

import pandas as pd


def parse_tick_payload(payload: dict) -> list[dict]:
    history = payload.get("history") if isinstance(payload, dict) else None
    if not isinstance(history, dict):
        raise ValueError("Tick history must contain times and prices of the same length")
    times = history.get("times")
    prices = history.get("prices")
    if not isinstance(times, list) or not isinstance(prices, list) or len(times) != len(prices):
        raise ValueError("Tick history must contain times and prices of the same length")
    ticks = []
    for epoch, quote in zip(times, prices):
        if isinstance(epoch, bool) or isinstance(quote, bool):
            raise ValueError("Tick epoch and price must be numbers")
        ticks.append({"epoch": int(epoch), "quote": float(quote)})
    return ticks


def ticks_to_candles(ticks: list[dict], step: int = 60) -> list[dict]:
    """A minute is kept only when every second in that minute has a real print."""
    buckets: dict[int, dict[int, float]] = {}
    for row in ticks:
        epoch = int(row["epoch"])
        bucket = epoch // step * step
        buckets.setdefault(bucket, {})[epoch] = float(row["quote"])
    candles = []
    for bucket in sorted(buckets):
        quotes = buckets[bucket]
        if set(quotes) != set(range(bucket, bucket + step)):
            continue
        ordered = [quotes[epoch] for epoch in range(bucket, bucket + step)]
        candles.append({
            "epoch": bucket,
            "open": ordered[0],
            "high": max(ordered),
            "low": min(ordered),
            "close": ordered[-1],
        })
    return candles


def append_live_ticks(path: str | Path, rows: list[dict]) -> dict:
    """Append unseen prints. The same epoch is not stored twice."""
    target = Path(path)
    stored: dict[int, float] = {}
    if target.exists():
        frame = pd.read_parquet(target)
        for epoch, quote in zip(frame["epoch"], frame["quote"]):
            stored[int(epoch)] = float(quote)
    duplicates = 0
    for row in rows:
        epoch = int(row["epoch"])
        if epoch in stored:
            duplicates += 1
            continue
        stored[epoch] = float(row["quote"])
    target.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [{"epoch": epoch, "quote": stored[epoch]} for epoch in sorted(stored)]
    ).to_parquet(target, index=False)
    return {"ticks": len(stored), "duplicates": duplicates}
