"""Non-overlapping / embargo sample spacing."""

import pandas as pd

from app.features.dataset import embargo_overlapping_samples


def test_embargo_keeps_non_overlapping():
    # Entries every 60s; horizon 540 → keep every 9th
    epochs = list(range(1_700_000_000, 1_700_000_000 + 60 * 30, 60))
    times = pd.to_datetime(epochs, unit="s", utc=True)
    keep = embargo_overlapping_samples(times, horizon_seconds=540)
    kept_epochs = [epochs[i] for i, k in enumerate(keep.tolist()) if k]
    assert len(kept_epochs) >= 3
    for a, b in zip(kept_epochs, kept_epochs[1:]):
        assert b - a >= 540


def test_embargo_empty():
    keep = embargo_overlapping_samples(pd.Series(dtype="datetime64[ns, UTC]"), 540)
    assert keep.empty
