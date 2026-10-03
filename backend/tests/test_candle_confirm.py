"""Candle confirmation scores agree with clear directional OHLC structure."""

from app.strategies.candle_confirm import evaluate_candle_confirm


def _synthetic_up_ticks(n: int = 3600, start: float = 100.0):
    # 1 tick/sec upward drift for ~1h → enough 1m/5m candles
    out = []
    price = start
    for i in range(n):
        price += 0.01
        out.append((1_700_000_000 + i, price))
    return out


def _synthetic_down_ticks(n: int = 3600, start: float = 100.0):
    out = []
    price = start
    for i in range(n):
        price -= 0.01
        out.append((1_700_000_000 + i, price))
    return out


def test_candle_confirm_upper_on_uptrend():
    res = evaluate_candle_confirm(_synthetic_up_ticks(), "upper", min_score=3.0, min_gap=0.5)
    assert res.confirmed is True
    assert res.score > res.opposite_score


def test_candle_confirm_rejects_wrong_side():
    res = evaluate_candle_confirm(_synthetic_up_ticks(), "lower", min_score=4.0, min_gap=1.0)
    assert res.confirmed is False
