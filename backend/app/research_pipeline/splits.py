"""Chronological splits and purge rules.

Purge width is entry time plus the prediction horizon, not a fixed row count.
"""

from __future__ import annotations

import numpy as np


def dependence_block_length(horizon_seconds: int, sample_every_seconds: int) -> int:
    step = max(1, int(sample_every_seconds))
    return max(1, int(np.ceil(int(horizon_seconds) / step)))


def final_split(
    epochs: np.ndarray,
    horizon_seconds: int,
    test_fraction: float = 0.2,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    """Return dev, test, and purged boolean masks, plus the test start time.

    The newest `test_fraction` of the time span is the untouched test.
    A development row is purged when its label window reaches into that span.
    """
    epochs = np.asarray(epochs, dtype=float)
    t0 = float(epochs.min())
    t1 = float(epochs.max())
    test_start = t0 + (1.0 - float(test_fraction)) * (t1 - t0)
    test = epochs >= test_start
    purged = (~test) & ((epochs + int(horizon_seconds)) > test_start)
    dev = (~test) & ~purged
    return dev, test, purged, float(test_start)


def walk_forward_masks(
    epochs: np.ndarray,
    horizon_seconds: int,
    n_blocks: int = 4,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Expanding chronological folds inside an already purged development set."""
    epochs = np.asarray(epochs, dtype=float)
    if len(epochs) == 0:
        return []
    t0 = float(epochs.min())
    t1 = float(epochs.max())
    edges = np.linspace(t0, t1 + 1e-6, int(n_blocks) + 1)
    folds: list[tuple[np.ndarray, np.ndarray]] = []
    for k in range(1, int(n_blocks)):
        val = (epochs >= edges[k]) & (epochs < edges[k + 1])
        train = epochs + int(horizon_seconds) <= edges[k]
        if int(val.sum()) == 0 or int(train.sum()) == 0:
            continue
        folds.append((train, val))
    return folds
