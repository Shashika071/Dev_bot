"""Regression coverage for the 2026-10-17 research review repairs."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.features.snapshot import build_decision_snapshot, decision_frame
from app.ml.promotion import promotion_blockers
from app.ml.calibration import ProbabilityCalibrator


def _ticks(n: int = 800, start: int = 1_700_000_000) -> pd.DataFrame:
    epochs = np.arange(start, start + n, dtype=np.int64)
    quotes = 100.0 + np.cumsum(np.random.default_rng(0).normal(0, 0.01, size=n))
    return pd.DataFrame(
        {
            "epoch": epochs,
            "quote": quotes,
            "tick_time": pd.to_datetime(epochs, unit="s", utc=True),
        }
    )


def test_decision_frame_binary_search_matches_filter():
    ticks = _ticks(2000)
    epoch = int(ticks["epoch"].iloc[1500])
    fast = decision_frame(ticks, epoch, history=600)
    slow = ticks.loc[ticks["epoch"] <= epoch].tail(600).copy()
    assert len(fast) == len(slow) == 600
    np.testing.assert_array_equal(fast["epoch"].to_numpy(), slow["epoch"].to_numpy())
    np.testing.assert_allclose(fast["quote"].to_numpy(), slow["quote"].to_numpy())


def test_snapshot_ready_on_sorted_history():
    ticks = _ticks(700)
    epoch = int(ticks["epoch"].iloc[-1])
    snap = build_decision_snapshot(
        ticks,
        epoch,
        barrier_distance=0.09,
        barrier_direction="upper",
        duration_seconds=540,
        seq_len=64,
    )
    assert snap.ready
    assert snap.sequence is not None
    assert snap.sequence.shape[0] == 64


def test_promotion_blockers_require_gates():
    assert promotion_blockers({}) == ["missing training metadata"]
    blocked = promotion_blockers({"promotion_ready": False, "promotion_checks": {}})
    assert any("gates" in r for r in blocked)
    ok = promotion_blockers(
        {
            "promotion_ready": True,
            "promotion_checks": {
                "min_samples": True,
                "calibration": True,
                "stability": True,
                "beats_baseline": True,
            },
        }
    )
    assert ok == []


def test_calibrator_prefers_platt_on_small_sets():
    rng = np.random.default_rng(1)
    raw = rng.uniform(0.2, 0.8, size=80)
    y = (raw > 0.5).astype(int)
    # Ensure both classes
    y[0] = 0
    y[1] = 1
    cal = ProbabilityCalibrator(method="auto", min_samples=20)
    cal.fit(raw, y)
    assert cal.method == "platt"


@pytest.mark.asyncio
async def test_tick_collector_rejects_symbol_mismatch(monkeypatch):
    from app.collector import tick_collector as tc

    class FakeClient:
        async def subscribe_ticks(self, *a, **k):
            return None

    calls = {"persist": 0}

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, *a, **k):
            calls["persist"] += 1

        async def commit(self):
            return None

    monkeypatch.setattr(tc, "async_session", lambda: FakeSession())
    collector = tc.TickCollector(FakeClient(), "R_100")
    collector._running = True
    await collector._handle_tick(
        {"tick": {"symbol": "R_50", "epoch": 1_700_000_100, "quote": 123.45}}
    )
    assert calls["persist"] == 0
    # Matching symbol proceeds to persist path
    await collector._handle_tick(
        {"tick": {"symbol": "R_100", "epoch": 1_700_000_100, "quote": 123.45}}
    )
    assert calls["persist"] == 1
