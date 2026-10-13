import numpy as np
import pandas as pd

from app.ml.direction_task import (
    build_direction_rows,
    closed_candle_features,
    direction_label,
    train_direction_task,
)
from app.signal_engine.chart_candles import aggregate_ohlc


def test_direction_label_uses_atr_band():
    assert direction_label(100, 100.01, atr=0.1, band_mult=0.25) == "neutral"
    assert direction_label(100, 101, atr=0.1, band_mult=0.25) == "up"
    assert direction_label(100, 99, atr=0.1, band_mult=0.25) == "down"


def test_closed_candle_feature_ignores_the_forming_bar():
    candles = aggregate_ohlc(
        [(index, 100 if index < 3600 else 500) for index in range(0, 4200, 2)],
        "1m",
        max_candles=0,
    )
    features = closed_candle_features(candles, decision_epoch=3600, step=60)
    assert features
    assert abs(features["c1_return"]) < 0.05


def test_direction_rows_do_not_use_future_candle_features():
    epochs = np.arange(0, 20000, 2)
    quotes = 100 + np.sin(epochs / 40)
    frame = pd.DataFrame({"epoch": epochs, "quote": quotes})
    rows = build_direction_rows(frame, 300, spacing_seconds=300)
    assert not rows.empty
    assert rows["entry_epoch"].is_monotonic_increasing
    assert set(rows["label"]).issubset({"up", "down", "neutral"})


def test_direction_training_stays_inactive_until_gates_pass(tmp_path, monkeypatch):
    monkeypatch.setattr("app.ml.direction_task.settings.model_dir", str(tmp_path))
    epochs = np.arange(0, 30000, 2)
    quotes = 100 + np.sin(epochs / 25) * 1.5
    frame = pd.DataFrame({"epoch": epochs, "quote": quotes, "tick_time": pd.to_datetime(epochs, unit="s", utc=True)})
    result = train_direction_task(
        frame,
        "direction_5m",
        min_samples=30,
        spacing_seconds=120,
    )
    assert "metadata" in result
    assert result["metadata"]["task_id"] == "direction_5m"
    assert "promotion_checks" in result["metadata"]
    assert result["metadata"]["promotion_ready"] in {True, False}
