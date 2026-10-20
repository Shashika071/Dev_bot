"""Chart-only Binary Campus confirmation: structure, ROC, and TSI."""
from app.synthetic.trend_confirmation import _crossed_over, _crossed_under, _roc, trend_confirmation


def _candle(index: int, price: float) -> dict:
    return {"epoch": 1_700_000_000 + index * 300, "open": price, "high": price + 0.4, "low": price - 0.4, "close": price}


def test_roc_is_percent_change_over_its_length():
    values = _roc([10, 10, 11, 12], 2)
    assert values[0] is None and values[1] is None
    assert values[2] == 10
    assert values[3] == 20


def test_cross_helpers_match_same_candle_rule():
    assert _crossed_over(0.2, -0.1)
    assert not _crossed_over(0.2, 0.1)
    assert _crossed_under(-0.2, 0.1)
    assert not _crossed_under(-0.2, -0.3)


def test_trend_marks_use_candle_times_and_dashboard_labels():
    candles = [_candle(index, 100 + (index % 7) - 3) for index in range(80)]
    result = trend_confirmation(candles)
    epochs = {row["epoch"] for row in candles}
    assert set(result["dashboard"]) == {"trend", "roc", "tsi", "mode"}
    assert result["dashboard"]["trend"] in {"BULLISH (UP)", "BEARISH (DOWN)", "NEUTRAL"}
    assert result["dashboard"]["mode"] == "Exact Same Candle"
    assert all(mark["epoch"] in epochs and mark["side"] in {"BUY", "SELL"} for mark in result["signals"])
