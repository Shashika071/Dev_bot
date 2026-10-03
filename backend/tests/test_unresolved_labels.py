"""Insufficient-window labels are unresolved, not silent losses."""

from datetime import datetime, timedelta, timezone

import pandas as pd

from app.features.labels import filter_resolved_labels, label_touch_outcomes


def test_insufficient_ticks_unresolved():
    base = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    ticks = pd.DataFrame([
        {"epoch": int(base.timestamp()), "tick_time": base, "quote": 100.0},
        # Only 1 tick in window — unresolved when min_window_ticks=5
        {"epoch": int(base.timestamp()) + 10, "tick_time": base + timedelta(seconds=10), "quote": 100.2},
    ])
    out = label_touch_outcomes(
        ticks, pd.Series([base]), duration_seconds=540, barrier_distance=0.2,
        barrier_direction="upper", min_window_ticks=5,
    )
    assert bool(out.iloc[0]["resolved"]) is False
    assert pd.isna(out.iloc[0]["touched"])
    filtered = filter_resolved_labels(out)
    assert len(filtered) == 0


def test_entry_delay_shifts_window():
    base = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    ticks = pd.DataFrame([
        {"epoch": int(base.timestamp()), "tick_time": base, "quote": 100.0},
        # Touch during delay — should NOT count
        {"epoch": int(base.timestamp()) + 2, "tick_time": base + timedelta(seconds=2), "quote": 100.3},
        # After delay
        {"epoch": int(base.timestamp()) + 10, "tick_time": base + timedelta(seconds=10), "quote": 100.0},
        {"epoch": int(base.timestamp()) + 20, "tick_time": base + timedelta(seconds=20), "quote": 100.0},
        {"epoch": int(base.timestamp()) + 30, "tick_time": base + timedelta(seconds=30), "quote": 100.0},
        {"epoch": int(base.timestamp()) + 40, "tick_time": base + timedelta(seconds=40), "quote": 100.0},
        {"epoch": int(base.timestamp()) + 50, "tick_time": base + timedelta(seconds=50), "quote": 100.0},
    ])
    out = label_touch_outcomes(
        ticks, pd.Series([base]), duration_seconds=540, barrier_distance=0.2,
        barrier_direction="upper", entry_delay_seconds=5, min_window_ticks=5,
    )
    assert bool(out.iloc[0]["resolved"]) is True
    assert bool(out.iloc[0]["touched"]) is False
