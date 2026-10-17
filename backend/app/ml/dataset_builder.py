"""Shared Touch dataset construction for training and replay."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np
import pandas as pd

from app.features.labels import filter_resolved_labels, label_touch_outcomes
from app.features.pipeline import select_feature_matrix, validate_no_future_leakage
from app.features.sequences import stack_sequences
from app.features.snapshot import build_decision_snapshot


ProgressFn = Callable[[str], None]


@dataclass
class TouchDataset:
    features: pd.DataFrame
    sequences: np.ndarray
    labels: pd.Series
    times: pd.Series
    unresolved: int
    resolved_before_alignment: int
    leakage: bool
    aligned_rows: int


def build_touch_dataset(
    ticks_df: pd.DataFrame,
    entry_times: pd.Series,
    *,
    barrier_distance: float,
    barrier_direction: str,
    duration_seconds: int,
    entry_delay_seconds: int,
    seq_len: int,
    min_window_ticks: int,
    on_progress: Optional[ProgressFn] = None,
) -> TouchDataset:
    """Label entries and build inclusive decision snapshots for each one."""

    def _progress(msg: str) -> None:
        if on_progress is not None:
            try:
                on_progress(msg)
            except Exception:
                pass

    _progress(f"Labeling {len(entry_times):,} candidate entries…")
    t0 = time.monotonic()
    # Ensure epochs are sorted once for binary-search snapshots.
    ticks = ticks_df.sort_values("epoch").reset_index(drop=True)
    labels_df = label_touch_outcomes(
        ticks_df=ticks,
        entry_times=entry_times,
        duration_seconds=duration_seconds,
        barrier_distance=barrier_distance,
        barrier_direction=barrier_direction,
        entry_delay_seconds=entry_delay_seconds,
        min_window_ticks=min_window_ticks,
    )
    unresolved = int((~labels_df["resolved"]).sum()) if not labels_df.empty else 0
    resolved = filter_resolved_labels(labels_df)
    _progress(
        f"Labels done in {time.monotonic() - t0:.1f}s — "
        f"{len(resolved):,} resolved, {unresolved:,} unresolved. Building snapshots…"
    )
    rows: list[dict] = []
    feature_epochs: list[int] = []
    entry_epochs: list[int] = []
    sequences: list[np.ndarray] = []
    total = len(resolved)
    report_every = max(1, total // 20) if total else 1

    for i, (_, row) in enumerate(resolved.iterrows()):
        entry_time = row["entry_time"]
        entry_epoch = (
            int(entry_time.timestamp())
            if hasattr(entry_time, "timestamp")
            else int(entry_time)
        )
        snap = build_decision_snapshot(
            ticks,
            entry_epoch,
            barrier_distance=barrier_distance,
            barrier_direction=barrier_direction,
            duration_seconds=duration_seconds,
            seq_len=seq_len,
        )
        if not snap.ready or snap.sequence is None or snap.features.empty:
            continue
        feat_row = snap.features.iloc[-1].to_dict()
        feat_epoch = int(feat_row.get("epoch", snap.ticks["epoch"].iloc[-1]))
        feature_epochs.append(feat_epoch)
        entry_epochs.append(entry_epoch)
        sequences.append(snap.sequence)
        rows.append(
            {
                "features": feat_row,
                "touched": int(bool(row["touched"])),
                "entry_time": entry_time,
            }
        )
        if (i + 1) % report_every == 0 or (i + 1) == total:
            _progress(
                f"Snapshots {i + 1:,}/{total:,} "
                f"({100.0 * (i + 1) / max(1, total):.0f}%) — "
                f"{len(rows):,} aligned · {time.monotonic() - t0:.0f}s"
            )

    leakage_ok = validate_no_future_leakage(feature_epochs, entry_epochs)
    if not rows:
        empty_x = pd.DataFrame()
        return TouchDataset(
            features=empty_x,
            sequences=stack_sequences([]),
            labels=pd.Series(dtype=int, name="touched"),
            times=pd.Series(dtype="datetime64[ns, UTC]", name="entry_time"),
            unresolved=unresolved,
            resolved_before_alignment=int(len(resolved)),
            leakage=not leakage_ok,
            aligned_rows=0,
        )

    features = select_feature_matrix(pd.DataFrame([r["features"] for r in rows]))
    _progress(
        f"Dataset ready: {len(rows):,} aligned rows in {time.monotonic() - t0:.1f}s"
    )
    return TouchDataset(
        features=features,
        sequences=stack_sequences(sequences),
        labels=pd.Series([r["touched"] for r in rows], name="touched"),
        times=pd.Series([r["entry_time"] for r in rows], name="entry_time"),
        unresolved=unresolved,
        resolved_before_alignment=int(len(resolved)),
        leakage=not leakage_ok,
        aligned_rows=len(rows),
    )
