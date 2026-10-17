import pandas as pd
import pytest
from datetime import datetime, timezone, timedelta

from app.features.labels import TOUCH_LABEL_SEMANTICS, label_touch_outcomes


def _ticks(base: datetime, points: list[tuple[int, float]]) -> pd.DataFrame:
    rows = []
    for offset, quote in points:
        ts = base + timedelta(seconds=offset)
        rows.append({"epoch": int(ts.timestamp()), "tick_time": ts, "quote": quote})
    return pd.DataFrame(rows)


def test_upper_touch_triggered():
    base_time = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    ticks = _ticks(
        base_time,
        [
            (-10, 99.0),
            (0, 100.0),
            (2, 100.0),  # next tick supplies the entry spot
            (10, 100.05),
            (20, 100.10),
            (30, 99.5),
            (540, 99.5),
        ],
    )
    results = label_touch_outcomes(
        ticks,
        pd.Series([base_time]),
        duration_seconds=540,
        barrier_distance=0.09,
        barrier_direction="upper",
        barrier_type="relative",
        max_gap_seconds=600,
    )
    assert len(results) == 1
    assert bool(results.iloc[0]["resolved"]) is True
    assert bool(results.iloc[0]["touched"]) is True
    assert results.iloc[0]["barrier_level"] == 100.09
    assert results.iloc[0]["touch_time"] == pd.Timestamp(base_time + timedelta(seconds=20))
    assert results.iloc[0]["touch_price"] == 100.10


def test_lower_touch_triggered():
    base_time = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    ticks = _ticks(
        base_time,
        [
            (0, 100.0),
            (2, 100.0),
            (10, 99.95),
            (20, 99.90),
            (540, 100.0),
        ],
    )
    results = label_touch_outcomes(
        ticks,
        pd.Series([base_time]),
        duration_seconds=540,
        barrier_distance=0.09,
        barrier_direction="lower",
        barrier_type="relative",
        max_gap_seconds=600,
    )
    assert bool(results.iloc[0]["resolved"]) is True
    assert bool(results.iloc[0]["touched"]) is True
    assert results.iloc[0]["barrier_level"] == 99.91
    assert results.iloc[0]["touch_time"] == pd.Timestamp(base_time + timedelta(seconds=20))


def test_touch_after_window_expires_is_ignored():
    base_time = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    ticks = _ticks(
        base_time,
        [
            (0, 100.0),
            (10, 100.02),
            (20, 100.03),
            (30, 100.04),
            (40, 100.05),
            (50, 100.05),
            (55, 100.04),
            (60, 100.03),
            (70, 100.15),  # after expiry
        ],
    )
    results = label_touch_outcomes(
        ticks,
        pd.Series([base_time]),
        duration_seconds=60,
        barrier_distance=0.09,
        barrier_direction="upper",
        barrier_type="relative",
        max_gap_seconds=15,
    )
    assert bool(results.iloc[0]["resolved"]) is True
    assert bool(results.iloc[0]["touched"]) is False
    assert pd.isna(results.iloc[0]["touch_time"])


def test_delayed_entry_anchors_barrier_to_accepted_spot():
    """Decision spot 100, delayed entry 101, barrier +0.2 → needs 101.2, not 100.2."""
    base = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    ticks = _ticks(
        base,
        [
            (0, 100.0),
            (5, 101.0),
            (10, 101.0),
            (20, 101.0),
            (30, 101.0),
            (40, 101.0),
            (50, 101.0),
            (65, 101.0),
        ],
    )
    out = label_touch_outcomes(
        ticks,
        pd.Series([base]),
        duration_seconds=60,
        barrier_distance=0.2,
        barrier_direction="upper",
        entry_delay_seconds=5,
        min_window_ticks=5,
        max_gap_seconds=15,
    )
    row = out.iloc[0]
    assert bool(row["resolved"]) is True
    assert float(row["entry_price"]) == 101.0
    assert float(row["barrier_level"]) == 101.2
    assert bool(row["touched"]) is False
    assert int(row["decision_epoch"]) == int(base.timestamp())
    assert int(row["processing_start_epoch"]) == int(base.timestamp()) + 5
    assert int(row["actual_entry_epoch"]) == int(base.timestamp()) + 10
    assert int(row["expiry_epoch"]) == int(base.timestamp()) + 65
    assert row["label_semantics"] == TOUCH_LABEL_SEMANTICS


def test_incomplete_future_is_unresolved_not_a_loss():
    base = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    ticks = _ticks(
        base,
        [
            (0, 100.0),
            (2, 100.0),
            (10, 100.0),
            (20, 100.0),
            (30, 100.0),
            (40, 100.0),
            (50, 100.0),
            # dataset ends here; no path to second 540
        ],
    )
    out = label_touch_outcomes(
        ticks,
        pd.Series([base]),
        duration_seconds=540,
        barrier_distance=0.2,
        barrier_direction="upper",
        min_window_ticks=5,
    )
    assert bool(out.iloc[0]["resolved"]) is False
    assert out.iloc[0]["unresolved_reason"] == "incomplete_future"
    assert pd.isna(out.iloc[0]["touched"])


@pytest.mark.parametrize("direction, anchor, later, expected", [
    ("upper", 100.2, 100.205, False),
    ("lower", 99.8, 99.795, False),
    ("upper", 99.9, 99.92, True),
    ("lower", 100.1, 100.08, True),
])
def test_relative_barrier_uses_next_tick_not_decision_spot(direction, anchor, later, expected):
    """Moving the entry anchor changes both misses and hits for a close barrier."""
    base = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    ticks = _ticks(base, [(0, 100.0), (2, anchor), (4, later), (6, anchor)])
    row = label_touch_outcomes(
        ticks, pd.Series([base]), duration_seconds=6,
        barrier_distance=0.01, barrier_direction=direction,
    ).iloc[0]
    assert bool(row["resolved"])
    assert bool(row["touched"]) is expected
    assert row["entry_time"] == base  # snapshots must remain at decision time
    assert row["entry_price"] == anchor
    assert row["actual_entry_epoch"] == int(base.timestamp()) + 2
    assert row["ticks_in_window"] == 2  # the anchor is not a future outcome tick


def test_tick_at_processing_start_is_not_the_entry_anchor():
    base = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    ticks = _ticks(base, [(0, 99.0), (5, 100.0), (6, 101.0), (8, 101.0), (11, 101.0)])
    row = label_touch_outcomes(
        ticks, pd.Series([base]), duration_seconds=6, entry_delay_seconds=5,
        barrier_distance=0.01,
    ).iloc[0]
    assert bool(row["resolved"])
    assert row["processing_start_epoch"] == int(base.timestamp()) + 5
    assert row["actual_entry_epoch"] == int(base.timestamp()) + 6
    assert row["entry_price"] == 101.0
    assert not bool(row["touched"])


def test_waiting_for_anchor_does_not_extend_expiry():
    base = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    ticks = _ticks(base, [(0, 100.0), (2, 100.0), (4, 100.0), (6, 100.0), (8, 100.02)])
    row = label_touch_outcomes(
        ticks, pd.Series([base]), duration_seconds=6, barrier_distance=0.01,
    ).iloc[0]
    assert bool(row["resolved"])
    assert row["expiry_epoch"] == int(base.timestamp()) + 6
    assert not bool(row["touched"])


def test_touch_on_expiry_tick_counts():
    base = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    ticks = _ticks(base, [(0, 100.0), (2, 100.0), (4, 100.0), (6, 100.01)])
    row = label_touch_outcomes(
        ticks, pd.Series([base]), duration_seconds=6, barrier_distance=0.01,
    ).iloc[0]
    assert bool(row["resolved"])
    assert bool(row["touched"])
    assert row["touch_time"] == pd.Timestamp(base + timedelta(seconds=6))


@pytest.mark.parametrize("direction, anchor, boundary", [
    ("upper", 100.2, 100.21),
    ("lower", 100.22, 100.21),
])
def test_decimal_barrier_equality_is_a_touch(direction, anchor, boundary):
    base = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    ticks = _ticks(base, [(0, 100.0), (2, anchor), (4, boundary), (6, anchor)])
    row = label_touch_outcomes(
        ticks, pd.Series([base]), duration_seconds=6,
        barrier_distance=0.01, barrier_direction=direction,
    ).iloc[0]
    assert bool(row["resolved"])
    assert row["barrier_level"] == boundary
    assert bool(row["touched"])


def test_entry_anchor_is_excluded_even_for_an_absolute_barrier():
    base = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    ticks = _ticks(base, [(0, 100.0), (2, 101.0), (4, 100.0), (6, 100.0)])
    row = label_touch_outcomes(
        ticks, pd.Series([base]), duration_seconds=6,
        barrier_distance=100.5, barrier_type="absolute",
    ).iloc[0]
    assert bool(row["resolved"])
    assert not bool(row["touched"])


def test_anchor_at_expiry_has_no_resolvable_outcome_window():
    base = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    ticks = _ticks(base, [(0, 100.0), (6, 100.2), (8, 100.3)])
    row = label_touch_outcomes(
        ticks, pd.Series([base]), duration_seconds=6, barrier_distance=0.01,
    ).iloc[0]
    assert not bool(row["resolved"])
    assert row["unresolved_reason"] == "no_post_entry_window"
    assert row["processing_start_epoch"] == int(base.timestamp())
    assert row["expiry_epoch"] == int(base.timestamp()) + 6
    assert pd.isna(row["touched"])
