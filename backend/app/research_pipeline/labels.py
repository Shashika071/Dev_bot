"""Labels from raw ticks.

Window rule, matching the live touch labeler when the path is complete:
observation ticks are those with epoch > entry + delay and epoch <= entry + horizon.
Upper and lower touches are separate. A gap, or a series that never reaches
expiry, drops the row. These labels are path outcomes, not broker settlement.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.research_pipeline.settings import ExperimentConfig


def decision_times(epochs: np.ndarray, cfg: ExperimentConfig, *, end_epoch: int | None = None) -> list[int]:
    epochs = np.asarray(epochs, dtype=np.int64)
    if len(epochs) == 0:
        return []
    start = int(epochs[0]) + int(cfg.warmup_seconds)
    last = int(epochs[-1]) if end_epoch is None else int(end_epoch)
    if last < start:
        return []
    step = int(cfg.sample_every_seconds)
    return list(range(start, last + 1, step))


def label_entries(
    epochs: np.ndarray,
    quotes: np.ndarray,
    entries: list[int] | np.ndarray,
    cfg: ExperimentConfig,
) -> pd.DataFrame:
    epochs = np.asarray(epochs, dtype=np.int64)
    quotes = np.asarray(quotes, dtype=float)
    order = np.argsort(epochs, kind="mergesort")
    epochs = epochs[order]
    quotes = quotes[order]
    rows = [
        _label_one(
            epochs,
            quotes,
            int(entry),
            horizon=int(cfg.horizon_seconds),
            delay=int(cfg.entry_delay_seconds),
            barrier=float(cfg.barrier_distance),
            max_gap=int(cfg.max_gap_seconds),
            tie_policy=cfg.tie_policy,
        )
        for entry in entries
    ]
    return pd.DataFrame(rows)


def _label_one(
    epochs: np.ndarray,
    quotes: np.ndarray,
    entry: int,
    *,
    horizon: int,
    delay: int,
    barrier: float,
    max_gap: int,
    tie_policy: str,
) -> dict:
    row = {
        "entry_epoch": entry,
        "status": "ok",
        "entry_price": np.nan,
        "y_upper": np.nan,
        "y_lower": np.nan,
        "y_direction": np.nan,
        "tie": False,
        "end_price": np.nan,
        "upper_level": np.nan,
        "lower_level": np.nan,
    }
    if delay >= horizon:
        row["status"] = "delay_exceeds_horizon"
        return row
    entry_idx = int(np.searchsorted(epochs, entry, side="right") - 1)
    if entry_idx < 0:
        row["status"] = "no_entry"
        return row
    if entry - int(epochs[entry_idx]) > max_gap:
        row["status"] = "stale_entry"
        return row
    entry_price = float(quotes[entry_idx])
    row["entry_price"] = entry_price
    row["upper_level"] = entry_price + barrier
    row["lower_level"] = entry_price - barrier
    window_end = entry + horizon
    end_idx = int(np.searchsorted(epochs, window_end, side="left"))
    if end_idx >= len(epochs):
        row["status"] = "incomplete_future"
        return row
    span = epochs[entry_idx : end_idx + 1]
    if len(span) >= 2 and int(np.diff(span).max()) > max_gap:
        row["status"] = "material_gap"
        return row
    window_start = entry + delay
    in_window = (epochs > window_start) & (epochs <= window_end)
    if not np.any(in_window):
        row["status"] = "incomplete_future"
        return row
    prices = quotes[in_window]
    end_price = float(prices[-1])
    row["end_price"] = end_price
    row["y_upper"] = float(np.any(prices >= entry_price + barrier))
    row["y_lower"] = float(np.any(prices <= entry_price - barrier))
    if end_price > entry_price:
        row["y_direction"] = 1.0
    elif end_price < entry_price:
        row["y_direction"] = 0.0
    else:
        row["tie"] = True
        if tie_policy == "up":
            row["y_direction"] = 1.0
        elif tie_policy == "down":
            row["y_direction"] = 0.0
        else:
            row["y_direction"] = np.nan
    return row
