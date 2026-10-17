"""One decision-time view of ticks, features, and sequences.

Training, live inference, manual analysis, and replay all use this contract:
the decision tick is included, history is the last 600 ticks at or before that
epoch, and the sequence ends on the same tick.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.features.pipeline import build_features
from app.features.sequences import build_price_sequence, build_price_sequence_from_frame

HISTORY_TICKS = 600
MIN_HISTORY_TICKS = 100


def as_epoch(value) -> int:
    if hasattr(value, "timestamp"):
        return int(value.timestamp())
    return int(value)


def decision_frame(
    ticks_df: pd.DataFrame,
    decision_epoch: int,
    history: int = HISTORY_TICKS,
) -> pd.DataFrame:
    """Ticks with epoch <= decision_epoch, capped to the live history length.

    Uses binary search when epochs are sorted ascending (training path).
    """
    if ticks_df is None or ticks_df.empty:
        return pd.DataFrame(columns=["epoch", "tick_time", "quote"])
    epoch = int(decision_epoch)
    hist = int(history)
    epochs = ticks_df["epoch"].to_numpy(dtype=np.int64, copy=False)
    # Fast path: sorted ascending → O(log n) cut + slice of last `history` rows.
    if len(epochs) > 1 and bool(np.all(epochs[1:] >= epochs[:-1])):
        end = int(np.searchsorted(epochs, epoch, side="right"))
        start = max(0, end - hist)
        if end <= 0:
            return pd.DataFrame(columns=ticks_df.columns)
        return ticks_df.iloc[start:end].copy()
    frame = ticks_df.loc[ticks_df["epoch"].astype(np.int64) <= epoch].tail(hist)
    return frame.copy()


@dataclass
class DecisionSnapshot:
    decision_epoch: int
    ticks: pd.DataFrame
    features: pd.DataFrame
    sequence: np.ndarray | None

    @property
    def ready(self) -> bool:
        return (
            self.features is not None
            and not self.features.empty
            and len(self.ticks) >= MIN_HISTORY_TICKS
        )


def build_decision_snapshot(
    ticks_df: pd.DataFrame,
    decision_epoch: int,
    *,
    barrier_distance: float,
    barrier_direction: str,
    duration_seconds: int,
    seq_len: int,
    history: int = HISTORY_TICKS,
) -> DecisionSnapshot:
    frame = decision_frame(ticks_df, decision_epoch, history=history)
    epoch = int(decision_epoch)
    if len(frame) < MIN_HISTORY_TICKS:
        return DecisionSnapshot(epoch, frame, pd.DataFrame(), None)
    features = build_features(
        frame,
        barrier_distance=float(barrier_distance),
        barrier_direction=barrier_direction,
    )
    # Prefer the already-sliced frame so we do not re-scan the full tick table.
    sequence = build_price_sequence_from_frame(
        frame,
        barrier_distance=float(barrier_distance),
        barrier_direction=barrier_direction,
        duration_seconds=int(duration_seconds),
        seq_len=int(seq_len),
    )
    if sequence is None:
        sequence = build_price_sequence(
            ticks_df,
            entry_epoch=epoch,
            barrier_distance=float(barrier_distance),
            barrier_direction=barrier_direction,
            duration_seconds=int(duration_seconds),
            seq_len=int(seq_len),
        )
    return DecisionSnapshot(epoch, frame, features, sequence)
