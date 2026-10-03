import pytest
import pandas as pd
from datetime import datetime, timezone, timedelta
import numpy as np

from app.features.labels import label_touch_outcomes


def test_upper_touch_triggered():
    """Verify that an upper touch is correctly detected when price crosses the barrier."""
    base_time = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    entry_price = 100.0
    barrier_distance = 0.09
    
    ticks = pd.DataFrame([
        # Before entry
        {"epoch": int(base_time.timestamp()) - 10, "tick_time": base_time - timedelta(seconds=10), "quote": 99.0},
        # Entry time tick
        {"epoch": int(base_time.timestamp()), "tick_time": base_time, "quote": entry_price},
        # Inside window, below barrier
        {"epoch": int(base_time.timestamp()) + 10, "tick_time": base_time + timedelta(seconds=10), "quote": 100.05},
        # Touch!
        {"epoch": int(base_time.timestamp()) + 20, "tick_time": base_time + timedelta(seconds=20), "quote": 100.10},
        # After touch, drops back down
        {"epoch": int(base_time.timestamp()) + 30, "tick_time": base_time + timedelta(seconds=30), "quote": 99.5},
    ])
    
    entries = pd.Series([base_time])
    
    results = label_touch_outcomes(
        ticks, entries, duration_seconds=540, barrier_distance=barrier_distance,
        barrier_direction="upper", barrier_type="relative"
    )
    
    assert len(results) == 1
    assert bool(results.iloc[0]["touched"]) is True
    assert results.iloc[0]["barrier_level"] == 100.09
    assert results.iloc[0]["touch_time"] == pd.Timestamp(base_time + timedelta(seconds=20))
    assert results.iloc[0]["touch_price"] == 100.10


def test_lower_touch_triggered():
    """Verify that a lower touch is correctly detected."""
    base_time = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    entry_price = 100.0
    barrier_distance = 0.09
    
    ticks = pd.DataFrame([
        {"epoch": int(base_time.timestamp()), "tick_time": base_time, "quote": entry_price},
        {"epoch": int(base_time.timestamp()) + 10, "tick_time": base_time + timedelta(seconds=10), "quote": 99.95},
        # Touch!
        {"epoch": int(base_time.timestamp()) + 20, "tick_time": base_time + timedelta(seconds=20), "quote": 99.90}, 
    ])
    
    entries = pd.Series([base_time])
    
    results = label_touch_outcomes(
        ticks, entries, duration_seconds=540, barrier_distance=barrier_distance,
        barrier_direction="lower", barrier_type="relative"
    )
    
    assert bool(results.iloc[0]["touched"]) is True
    assert results.iloc[0]["barrier_level"] == 99.91
    assert results.iloc[0]["touch_time"] == pd.Timestamp(base_time + timedelta(seconds=20))


def test_touch_after_window_expires_is_ignored():
    """Verify that a touch occurring after the duration expires is NOT a win."""
    base_time = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    entry_price = 100.0
    duration = 60  # 1 minute
    
    ticks = pd.DataFrame([
        {"epoch": int(base_time.timestamp()), "tick_time": base_time, "quote": entry_price},
        # Inside window, safe
        {"epoch": int(base_time.timestamp()) + 50, "tick_time": base_time + timedelta(seconds=50), "quote": 100.05},
        # Outside window, touched! (Should be ignored)
        {"epoch": int(base_time.timestamp()) + 70, "tick_time": base_time + timedelta(seconds=70), "quote": 100.15},
    ])
    
    entries = pd.Series([base_time])
    
    results = label_touch_outcomes(
        ticks, entries, duration_seconds=duration, barrier_distance=0.09,
        barrier_direction="upper", barrier_type="relative"
    )
    
    assert bool(results.iloc[0]["touched"]) is False
    assert pd.isna(results.iloc[0]["touch_time"])
