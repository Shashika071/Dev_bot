"""Chronological split purging at every train/val/cal/test boundary."""

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from app.features.dataset import chronological_split


def _make_dataset(n: int = 200, spacing: int = 60):
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    times = pd.Series([base + timedelta(seconds=i * spacing) for i in range(n)])
    X = pd.DataFrame({"f1": np.arange(n, dtype=float), "f2": np.arange(n, dtype=float) * 0.1})
    y = pd.Series((np.arange(n) % 2).astype(int))
    return X, y, times


def test_purge_all_boundaries_no_overlap():
    X, y, times = _make_dataset(200, spacing=60)
    outcome = 540
    gap = 600
    split = chronological_split(
        X, y, times,
        outcome_window_seconds=outcome,
        gap_seconds=gap,
        feature_columns=["f1", "f2"],
    )

    assert len(split.X_train) > 0
    assert len(split.X_val) > 0
    assert len(split.X_cal) > 0
    assert len(split.X_test) > 0
    assert split.purged_count > 0

    # Train outcome+gap must end before first val sample
    train_end_allowed = (
        split.train_times.max().timestamp() + outcome + gap
    )
    assert train_end_allowed <= split.val_times.min().timestamp() + 1e-6

    # Val outcome+gap before first cal
    val_end_allowed = split.val_times.max().timestamp() + outcome + gap
    assert val_end_allowed <= split.cal_times.min().timestamp() + 1e-6

    # Cal outcome+gap before first test
    cal_end_allowed = split.cal_times.max().timestamp() + outcome + gap
    assert cal_end_allowed <= split.test_times.min().timestamp() + 1e-6


def test_split_rejects_tiny_dataset():
    X, y, times = _make_dataset(10, spacing=60)
    with pytest.raises(ValueError):
        chronological_split(X, y, times, outcome_window_seconds=540, gap_seconds=600)
