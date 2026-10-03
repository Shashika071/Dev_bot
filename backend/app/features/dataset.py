"""
Dataset splitting with chronological ordering, purging, and gap enforcement.

Rules:
- Strictly chronological: train → validation → calibration → test
- Purge samples whose outcome windows overlap later evaluation periods
- Time gap at split boundaries to prevent information leakage
- Final test set remains untouched until final evaluation
"""

import pandas as pd
import numpy as np
import structlog
from typing import Optional
from dataclasses import dataclass

logger = structlog.get_logger(__name__)


@dataclass
class DatasetSplit:
    """Result of a chronological dataset split."""
    X_train: pd.DataFrame
    y_train: pd.Series
    X_val: pd.DataFrame
    y_val: pd.Series
    X_cal: pd.DataFrame
    y_cal: pd.Series
    X_test: pd.DataFrame
    y_test: pd.Series
    train_times: pd.Series
    val_times: pd.Series
    cal_times: pd.Series
    test_times: pd.Series
    purged_count: int
    gap_seconds: int


def _to_epoch(value) -> int:
    if hasattr(value, "timestamp"):
        return int(value.timestamp())
    return int(value)


def _purge_mask(
    times: pd.Series,
    start_idx: int,
    end_idx: int,
    next_start_time,
    outcome_window_seconds: int,
    gap_seconds: int,
) -> tuple[pd.Series, int]:
    """
    Keep samples in [start_idx, end_idx) whose outcome window + gap
    ends before the next split's start time.
    """
    if end_idx <= start_idx:
        return pd.Series(dtype=bool), 0

    next_start_epoch = _to_epoch(next_start_time)
    purge_threshold = next_start_epoch - outcome_window_seconds - gap_seconds

    mask = []
    purged = 0
    for i in range(start_idx, end_idx):
        sample_epoch = _to_epoch(times.iloc[i])
        keep = sample_epoch <= purge_threshold
        mask.append(keep)
        if not keep:
            purged += 1
    return pd.Series(mask), purged


def chronological_split(
    features_df: pd.DataFrame,
    labels: pd.Series,
    times: pd.Series,
    train_frac: float = 0.60,
    val_frac: float = 0.15,
    cal_frac: float = 0.10,
    test_frac: float = 0.15,
    outcome_window_seconds: int = 540,
    gap_seconds: int = 600,
    feature_columns: list[str] = None,
) -> DatasetSplit:
    """
    Split data chronologically with purging and gap enforcement at every boundary.
    """
    assert abs(train_frac + val_frac + cal_frac + test_frac - 1.0) < 0.01

    n = len(features_df)
    if n == 0:
        raise ValueError("Empty dataset for splitting")

    sort_idx = times.argsort()
    features_sorted = features_df.iloc[sort_idx].reset_index(drop=True)
    labels_sorted = labels.iloc[sort_idx].reset_index(drop=True)
    times_sorted = times.iloc[sort_idx].reset_index(drop=True)

    train_end = int(n * train_frac)
    val_end = int(n * (train_frac + val_frac))
    cal_end = int(n * (train_frac + val_frac + cal_frac))

    # Ensure each segment has a next-boundary timestamp
    if train_end >= n or val_end >= n or cal_end > n:
        raise ValueError("Dataset too small for requested split fractions")

    X_all = features_sorted[feature_columns] if feature_columns else features_sorted

    purged_total = 0

    # Train: purge rows that leak into validation
    train_mask, purged = _purge_mask(
        times_sorted, 0, train_end, times_sorted.iloc[train_end],
        outcome_window_seconds, gap_seconds,
    )
    purged_total += purged
    X_train = X_all.iloc[:train_end][train_mask.values].reset_index(drop=True)
    y_train = labels_sorted.iloc[:train_end][train_mask.values].reset_index(drop=True)
    train_times = times_sorted.iloc[:train_end][train_mask.values].reset_index(drop=True)

    # Val: purge rows that leak into calibration
    val_mask, purged = _purge_mask(
        times_sorted, train_end, val_end, times_sorted.iloc[val_end],
        outcome_window_seconds, gap_seconds,
    )
    purged_total += purged
    X_val = X_all.iloc[train_end:val_end][val_mask.values].reset_index(drop=True)
    y_val = labels_sorted.iloc[train_end:val_end][val_mask.values].reset_index(drop=True)
    val_times = times_sorted.iloc[train_end:val_end][val_mask.values].reset_index(drop=True)

    # Cal: purge rows that leak into test
    if cal_end < n:
        next_start = times_sorted.iloc[cal_end]
    else:
        # No test rows — keep all cal by using a far-future threshold
        last_epoch = _to_epoch(times_sorted.iloc[-1])
        next_start = last_epoch + outcome_window_seconds + gap_seconds + 1

    cal_mask, purged = _purge_mask(
        times_sorted, val_end, cal_end, next_start,
        outcome_window_seconds, gap_seconds,
    )
    purged_total += purged
    X_cal = X_all.iloc[val_end:cal_end][cal_mask.values].reset_index(drop=True)
    y_cal = labels_sorted.iloc[val_end:cal_end][cal_mask.values].reset_index(drop=True)
    cal_times = times_sorted.iloc[val_end:cal_end][cal_mask.values].reset_index(drop=True)

    # Test: untouched (final evaluation)
    X_test = X_all.iloc[cal_end:].reset_index(drop=True)
    y_test = labels_sorted.iloc[cal_end:].reset_index(drop=True)
    test_times = times_sorted.iloc[cal_end:].reset_index(drop=True)

    if len(X_train) == 0 or len(X_val) == 0 or len(X_test) == 0:
        raise ValueError(
            f"Split produced empty set after purging "
            f"(train={len(X_train)}, val={len(X_val)}, cal={len(X_cal)}, test={len(X_test)})"
        )

    logger.info(
        "dataset_split_complete",
        total=n,
        train=len(X_train),
        val=len(X_val),
        cal=len(X_cal),
        test=len(X_test),
        purged=purged_total,
        gap_seconds=gap_seconds,
    )

    return DatasetSplit(
        X_train=X_train, y_train=y_train,
        X_val=X_val, y_val=y_val,
        X_cal=X_cal, y_cal=y_cal,
        X_test=X_test, y_test=y_test,
        train_times=train_times,
        val_times=val_times,
        cal_times=cal_times,
        test_times=test_times,
        purged_count=purged_total,
        gap_seconds=gap_seconds,
    )


def walk_forward_splits(
    features_df: pd.DataFrame,
    labels: pd.Series,
    times: pd.Series,
    n_splits: int = 5,
    min_train_frac: float = 0.3,
    outcome_window_seconds: int = 540,
    gap_seconds: int = 600,
    feature_columns: list[str] = None,
) -> list[DatasetSplit]:
    """
    Generate walk-forward splits for time-series cross-validation.
    Each fold has an expanding training window and a fixed-size test window.
    """
    n = len(features_df)
    test_size = max(1, int(n * (1 - min_train_frac) / n_splits))
    splits = []

    for fold in range(n_splits):
        test_start = int(n * min_train_frac) + fold * test_size
        test_end = min(test_start + test_size, n)
        if test_start < 20 or test_end <= test_start:
            break

        train_f = test_start / test_end * 0.70
        val_f = test_start / test_end * 0.15
        cal_f = test_start / test_end * 0.15
        test_f = (test_end - test_start) / test_end
        total = train_f + val_f + cal_f + test_f

        try:
            fold_split = chronological_split(
                features_df.iloc[:test_end],
                labels.iloc[:test_end],
                times.iloc[:test_end],
                train_frac=train_f / total,
                val_frac=val_f / total,
                cal_frac=cal_f / total,
                test_frac=test_f / total,
                outcome_window_seconds=outcome_window_seconds,
                gap_seconds=gap_seconds,
                feature_columns=feature_columns,
            )
        except ValueError:
            continue

        splits.append(fold_split)
        logger.info(
            "walk_forward_fold",
            fold=fold,
            train=len(fold_split.X_train),
            test=len(fold_split.X_test),
        )

    return splits
