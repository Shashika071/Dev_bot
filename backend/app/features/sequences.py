"""
Price-sequence builders for LSTM models.

Sequences use only ticks strictly before the decision/entry time.
Contract fields (barrier distance, direction, duration) are appended as constants.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def build_price_sequence(
    ticks_df: pd.DataFrame,
    entry_epoch: int,
    barrier_distance: float,
    barrier_direction: str,
    duration_seconds: int,
    seq_len: int = 64,
) -> np.ndarray | None:
    """
    Return shape (seq_len, n_channels) or None if insufficient history.

    Channels:
      0: normalized return vs last pre-entry price
      1: barrier_distance / price (relative points scale)
      2: direction (+1 upper / -1 lower)
      3: duration_seconds / 540
    """
    recent = ticks_df[ticks_df["epoch"] < entry_epoch].tail(seq_len)
    if len(recent) < seq_len:
        return None

    prices = recent["quote"].astype(float).values
    last = prices[-1]
    if last == 0 or np.isnan(last):
        return None

    norm = (prices / last) - 1.0
    direction = 1.0 if barrier_direction == "upper" else -1.0
    barrier_rel = float(barrier_distance) / last
    duration_rel = float(duration_seconds) / 540.0

    seq = np.column_stack(
        [
            norm,
            np.full(seq_len, barrier_rel, dtype=float),
            np.full(seq_len, direction, dtype=float),
            np.full(seq_len, duration_rel, dtype=float),
        ]
    )
    return seq.astype(np.float32)


def stack_sequences(sequences: list[np.ndarray]) -> np.ndarray:
    """Stack list of (T, C) arrays into (N, T, C)."""
    if not sequences:
        return np.zeros((0, 0, 0), dtype=np.float32)
    return np.stack(sequences, axis=0)
