"""Insufficient-window and incomplete-path labels are unresolved, not silent losses."""

from datetime import datetime, timedelta, timezone

import pandas as pd

from app.features.labels import filter_resolved_labels, label_touch_outcomes


def test_insufficient_ticks_unresolved():
    base = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    ticks = pd.DataFrame(
        [
            {"epoch": int(base.timestamp()), "tick_time": base, "quote": 100.0},
            {
                "epoch": int(base.timestamp()) + 10,
                "tick_time": base + timedelta(seconds=10),
                "quote": 100.2,
            },
            {
                "epoch": int(base.timestamp()) + 540,
                "tick_time": base + timedelta(seconds=540),
                "quote": 100.0,
            },
        ]
    )
    out = label_touch_outcomes(
        ticks,
        pd.Series([base]),
        duration_seconds=540,
        barrier_distance=0.2,
        barrier_direction="upper",
        min_window_ticks=5,
        max_gap_seconds=600,
    )
    assert bool(out.iloc[0]["resolved"]) is False
    assert out.iloc[0]["unresolved_reason"] in {"insufficient_window_ticks", "material_gap"}
    assert pd.isna(out.iloc[0]["touched"])
    assert len(filter_resolved_labels(out)) == 0


def test_entry_delay_shifts_window_and_ignores_pre_entry_touch():
    base = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    rows = [{"epoch": int(base.timestamp()), "tick_time": base, "quote": 100.0}]
    # Touch during delay — must NOT count
    rows.append(
        {
            "epoch": int(base.timestamp()) + 2,
            "tick_time": base + timedelta(seconds=2),
            "quote": 100.3,
        }
    )
    for sec in range(5, 66, 5):
        rows.append(
            {
                "epoch": int(base.timestamp()) + sec,
                "tick_time": base + timedelta(seconds=sec),
                "quote": 100.0,
            }
        )
    ticks = pd.DataFrame(rows)
    out = label_touch_outcomes(
        ticks,
        pd.Series([base]),
        duration_seconds=60,
        barrier_distance=0.2,
        barrier_direction="upper",
        entry_delay_seconds=5,
        min_window_ticks=5,
        max_gap_seconds=10,
    )
    assert bool(out.iloc[0]["resolved"]) is True
    assert bool(out.iloc[0]["touched"]) is False
    assert float(out.iloc[0]["entry_price"]) == 100.0
