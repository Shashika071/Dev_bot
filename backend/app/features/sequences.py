"""
Price-sequence builders for LSTM models.

Sequences use ticks at or before the decision/entry time, matching tabular features.
Contract fields (barrier distance, direction, duration) are appended as constants.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def build_price_sequence_from_frame(
    frame: pd.DataFrame,
    *,
    barrier_distance: float,
    barrier_direction: str,
    duration_seconds: int,
    seq_len: int = 64,
) -> np.ndarray | None:
    """Build a sequence from an already-sliced decision frame (no full-table scan)."""
    if frame is None or frame.empty or len(frame) < int(seq_len):
        return None
    recent = frame.tail(int(seq_len))
    prices = recent["quote"].astype(float).to_numpy()
    last = float(prices[-1])
    if last == 0 or np.isnan(last):
        return None
    norm = (prices / last) - 1.0
    direction = 1.0 if barrier_direction == "upper" else -1.0
    barrier_rel = float(barrier_distance) / last
    duration_rel = float(duration_seconds) / 540.0
    n = int(seq_len)
    seq = np.column_stack(
        [
            norm,
            np.full(n, barrier_rel, dtype=float),
            np.full(n, direction, dtype=float),
            np.full(n, duration_rel, dtype=float),
        ]
    )
    return seq.astype(np.float32)


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
    epoch = int(entry_epoch)
    epochs = ticks_df["epoch"].to_numpy(dtype=np.int64, copy=False) if not ticks_df.empty else np.array([], dtype=np.int64)
    if len(epochs) > 1 and bool(np.all(epochs[1:] >= epochs[:-1])):
        end = int(np.searchsorted(epochs, epoch, side="right"))
        start = max(0, end - int(seq_len))
        if end - start < int(seq_len):
            return None
        recent = ticks_df.iloc[start:end]
    else:
        recent = ticks_df[ticks_df["epoch"] <= epoch].tail(seq_len)
    return build_price_sequence_from_frame(
        recent,
        barrier_distance=barrier_distance,
        barrier_direction=barrier_direction,
        duration_seconds=duration_seconds,
        seq_len=seq_len,
    )


def stack_sequences(sequences: list[np.ndarray]) -> np.ndarray:
    """Stack list of (T, C) arrays into (N, T, C)."""
    if not sequences:
        return np.zeros((0, 0, 0), dtype=np.float32)
    return np.stack(sequences, axis=0)
