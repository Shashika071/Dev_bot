"""Replay uses the same decision snapshot and does not place orders."""

import numpy as np
import pandas as pd

from app.ml.replay import TouchReplayRunner


def test_replay_is_deterministic_and_order_free():
    rng = np.random.default_rng(11)
    n = 160
    epochs = np.arange(1_800_000_000, 1_800_000_000 + n)
    ticks = pd.DataFrame({
        "epoch": epochs,
        "tick_time": pd.to_datetime(epochs, unit="s", utc=True),
        "quote": 3000 + np.cumsum(rng.normal(0, 0.01, n)),
    })
    entry = int(epochs[-1])
    runner = TouchReplayRunner(seq_len=64, duration_seconds=540)
    first = runner.run(
        ticks, entry_epochs=[entry], barrier_distance=0.09, direction="upper",
    )
    second = runner.run(
        ticks, entry_epochs=[entry], barrier_distance=0.09, direction="upper",
    )
    assert first["n"] == 1
    assert first["would_signal"] == 0
    assert first["decisions"][0]["ready"] is True
    assert first["decisions"][0]["calibrated_probability"] is None
    assert first["decisions"] == second["decisions"]
