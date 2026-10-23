"""Build a higher timeframe only from a complete run of lower candles."""
from __future__ import annotations


def resample_candles(candles: list[dict], source_step: int, target_step: int) -> list[dict]:
    if source_step <= 0 or target_step < source_step or target_step % source_step != 0:
        return []
    needed = target_step // source_step
    buckets: dict[int, dict[int, dict]] = {}
    for row in candles:
        try:
            epoch = int(row["epoch"])
        except (KeyError, TypeError, ValueError):
            continue
        if epoch % source_step != 0:
            continue
        bucket = epoch // target_step * target_step
        buckets.setdefault(bucket, {})[epoch] = row
    completed = []
    for bucket in sorted(buckets):
        rows = buckets[bucket]
        expected = [bucket + index * source_step for index in range(needed)]
        if any(epoch not in rows for epoch in expected):
            continue
        ordered = [rows[epoch] for epoch in expected]
        completed.append({
            "epoch": bucket,
            "open": float(ordered[0]["open"]),
            "high": max(float(row["high"]) for row in ordered),
            "low": min(float(row["low"]) for row in ordered),
            "close": float(ordered[-1]["close"]),
        })
    return completed
