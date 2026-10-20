"""History input integrity, isolation and bounded download parameters."""
from __future__ import annotations

import copy

import pytest

from app.synthetic.history import (
    clean_candles,
    history_root,
    history_status,
    load_candles,
    save_candles,
    span_summary,
    validate_days,
)


def candle(epoch: int, price: float = 100) -> dict:
    return {"epoch": epoch, "open": price, "close": price + 1, "high": price + 2, "low": price - 2}


def test_clean_history_drops_bad_ohlc_nonfinite_unaligned_and_forming_candles():
    valid = candle(300)
    malformed = [
        candle(601), candle(1200), {**candle(600), "low": 110},
        {**candle(600), "close": float("nan")}, {**candle(600), "open": -1},
        {**candle(600), "epoch": 600.1},
    ]
    assert clean_candles([*malformed, valid, copy.deepcopy(valid)], 300, now=1200) == [valid]


def test_store_isolated_merges_and_preserves_gaps(tmp_path):
    root = history_root(tmp_path)
    assert root == tmp_path / "synthetic_r100" / "history"
    save_candles(root, "15m", [candle(900), candle(2700)], now=4500)
    merged = save_candles(root, "15m", [candle(2700, 101), candle(3600)], now=4500)
    assert len(merged) == 3
    assert merged[1]["open"] == 101
    assert load_candles(root, "15m", now=4500) == merged
    assert span_summary(merged, 900)["gaps"] == 1
    assert not history_status(tmp_path)["ready_for_train"]


@pytest.mark.parametrize("value", [59, 91, 0, -1, 90.5, "90", True])
def test_download_days_are_bounded(value):
    with pytest.raises(ValueError):
        validate_days(value)


def test_download_days_allow_supported_window():
    assert validate_days(60) == 60
    assert validate_days(90) == 90
