"""Outcome resolver helpers use the same touch-label semantics."""

from datetime import datetime, timezone

import pandas as pd

from app.features.labels import label_touch_outcomes


def test_label_touch_upper_for_resolver_window():
    # Rising path touches +0.1 barrier
    epochs = list(range(1000, 1600))
    quotes = [100.0 + (i - 1000) * 0.001 for i in epochs]
    ticks = pd.DataFrame({
        "epoch": epochs,
        "tick_time": pd.to_datetime(epochs, unit="s", utc=True),
        "quote": quotes,
    })
    entry = datetime.fromtimestamp(1000, tz=timezone.utc)
    labels = label_touch_outcomes(
        ticks_df=ticks,
        entry_times=pd.Series([entry]),
        duration_seconds=540,
        barrier_distance=0.1,
        barrier_direction="upper",
        entry_delay_seconds=5,
        min_window_ticks=5,
    )
    assert not labels.empty
    assert bool(labels.iloc[0]["resolved"]) is True
    assert bool(labels.iloc[0]["touched"]) is True
