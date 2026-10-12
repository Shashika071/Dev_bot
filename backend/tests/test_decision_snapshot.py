"""Decision-time snapshots match between training and live scoring."""

import numpy as np
import pandas as pd

from app.features.pipeline import build_features, fit_feature_prep, apply_feature_prep, get_feature_columns
from app.features.snapshot import build_decision_snapshot


def _ticks(n=180):
    rng = np.random.default_rng(7)
    epochs = np.arange(1_700_000_000, 1_700_000_000 + n)
    quotes = 2500 + np.cumsum(rng.normal(0, 0.02, n))
    return pd.DataFrame({
        "epoch": epochs,
        "tick_time": pd.to_datetime(epochs, unit="s", utc=True),
        "quote": quotes,
    })


def test_snapshot_includes_decision_tick_and_is_repeatable():
    ticks = _ticks()
    decision = int(ticks["epoch"].iloc[-1])
    first = build_decision_snapshot(
        ticks, decision,
        barrier_distance=0.09, barrier_direction="upper",
        duration_seconds=540, seq_len=64,
    )
    second = build_decision_snapshot(
        ticks, decision,
        barrier_distance=0.09, barrier_direction="upper",
        duration_seconds=540, seq_len=64,
    )
    assert first.ready
    assert int(first.ticks["epoch"].iloc[-1]) == decision
    assert first.sequence is not None
    assert abs(float(first.sequence[-1, 0])) < 1e-6
    pd.testing.assert_series_equal(first.features.iloc[-1], second.features.iloc[-1])
    direct = build_features(
        ticks[ticks["epoch"] <= decision].tail(600),
        barrier_distance=0.09,
        barrier_direction="upper",
    )
    pd.testing.assert_series_equal(first.features.iloc[-1], direct.iloc[-1])


def test_variance_filter_and_regime_edges_use_train_only():
    rng = np.random.default_rng(3)
    rows = {name: rng.normal(size=40) for name in get_feature_columns()}
    train = pd.DataFrame(rows)
    train["barrier_distance_points"] = 0.09
    train["realized_vol_300"] = np.linspace(0.01, 0.3, 40)
    train["price_range_300"] = np.linspace(0.05, 0.4, 40)
    prep = fit_feature_prep(train)
    assert "barrier_distance_points" not in prep["feature_columns"]
    assert prep["vol_low"] is not None
    assert prep["range_median"] is not None

    shifted = train.copy()
    shifted["barrier_distance_points"] = np.linspace(0.1, 0.5, 40)
    applied = apply_feature_prep(shifted, prep)
    assert list(applied.columns) == prep["feature_columns"]
    assert "barrier_distance_points" not in applied.columns
