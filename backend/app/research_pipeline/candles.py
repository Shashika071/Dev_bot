"""Completed OHLC candles. Incomplete buckets are not emitted."""

from __future__ import annotations

import numpy as np
import pandas as pd


def candle_tables(
    epochs: np.ndarray,
    quotes: np.ndarray,
    step: int,
    max_gap_seconds: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Aggregate ticks into candles of `step` seconds.

    A bucket is [start, start+step). It is complete at start+step.
    Ticks exactly at start+step belong to the next bucket.
    Buckets with an internal tick gap above `max_gap_seconds` are flagged and
    omitted from the usable frame. Missing buckets are not filled.
    """
    epochs = np.asarray(epochs, dtype=np.int64)
    quotes = np.asarray(quotes, dtype=float)
    order = np.argsort(epochs, kind="mergesort")
    epochs = epochs[order]
    quotes = quotes[order]
    bucket = (epochs // int(step)) * int(step)
    frame = pd.DataFrame({"bucket": bucket, "epoch": epochs, "quote": quotes})
    frame["pos"] = frame.groupby("bucket", sort=False).cumcount()
    frame["tick_gap"] = frame["epoch"].diff().fillna(0)
    frame.loc[frame["pos"] == 0, "tick_gap"] = 0
    aggregated = (
        frame.groupby("bucket", sort=True)
        .agg(
            open=("quote", "first"),
            high=("quote", "max"),
            low=("quote", "min"),
            close=("quote", "last"),
            tick_count=("quote", "size"),
            max_internal_gap=("tick_gap", "max"),
        )
        .reset_index()
    )
    aggregated["close_time"] = aggregated["bucket"] + int(step)
    aggregated["gapped"] = aggregated["max_internal_gap"] > int(max_gap_seconds)
    usable = aggregated.loc[~aggregated["gapped"]].copy().reset_index(drop=True)
    buckets = usable["bucket"].to_numpy(dtype=np.int64)
    segment = np.zeros(len(usable), dtype=np.int64)
    for i in range(1, len(usable)):
        if buckets[i] - buckets[i - 1] == int(step):
            segment[i] = segment[i - 1]
        else:
            segment[i] = segment[i - 1] + 1
    usable["segment"] = segment
    return aggregated, usable


def completed_asof(candles: pd.DataFrame, asof: int) -> pd.DataFrame:
    """Candles whose close time is at or before `asof`."""
    if candles.empty:
        return candles.copy()
    return candles.loc[candles["close_time"] <= int(asof)].copy()
