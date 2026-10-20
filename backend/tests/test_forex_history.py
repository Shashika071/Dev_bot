"""History store helpers for EURUSD candle download."""
from pathlib import Path

from app.forex.history import (
    aggregate_candles,
    history_status,
    load_candles,
    save_candles,
    span_summary,
)


def _candle(epoch: int, close: float = 1.1) -> dict:
    return {"epoch": epoch, "open": close - 0.0001, "high": close + 0.0002,
            "low": close - 0.0002, "close": close}


def test_span_summary_empty_and_populated():
    assert span_summary([]) == {"bars": 0, "from_epoch": None, "to_epoch": None, "days": 0.0}
    rows = [_candle(1_000_000), _candle(1_000_000 + 2 * 86400)]
    summary = span_summary(rows)
    assert summary["bars"] == 2
    assert summary["days"] == 2.0
    assert summary["from_epoch"] == 1_000_000


def test_save_candles_merges_and_dedupes(tmp_path: Path):
    root = tmp_path / "frxEURUSD"
    first = [_candle(1000 + i * 900, 1.1 + i * 0.0001) for i in range(5)]
    second = [_candle(1000 + i * 900, 1.2 + i * 0.0001) for i in range(3, 8)]
    save_candles(root, "15m", first)
    merged = save_candles(root, "15m", second)
    assert len(merged) == 8
    assert merged[3]["close"] == second[0]["close"]
    reloaded = load_candles(root, "15m")
    assert reloaded == merged
    assert (root / "15m.parquet").exists()


def test_history_status_ready_requires_all_timeframes(tmp_path: Path, monkeypatch):
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    monkeypatch.setattr("app.forex.history.history_root", lambda _base: tmp_path / "hist")
    status = history_status(model_dir)
    assert status["ready_for_train"] is False
    root = tmp_path / "hist"
    for tf in ("15m", "1h", "4h"):
        save_candles(root, tf, [_candle(1_700_000_000 + i * 900) for i in range(500)])
    ready = history_status(model_dir)
    assert ready["ready_for_train"] is True
    assert ready["timeframes"]["15m"]["bars"] == 500


def test_aggregate_candles_builds_higher_timeframes():
    start = 1_700_000_000 - (1_700_000_000 % 14400)
    rows = [_candle(start + i * 900, 1.1 + i * 0.0001) for i in range(16)]
    hourly = aggregate_candles(rows, 3600, source_step=900)
    assert len(hourly) == 4
    assert hourly[0]["open"] == rows[0]["open"]
    assert hourly[0]["close"] == rows[3]["close"]
    assert hourly[0]["high"] == max(row["high"] for row in rows[:4])
    four_hour = aggregate_candles(rows, 14400, source_step=900)
    assert len(four_hour) == 1
    assert four_hour[0]["close"] == rows[-1]["close"]
    # Incomplete H4 bucket is dropped.
    assert aggregate_candles(rows[:15], 14400, source_step=900) == []
