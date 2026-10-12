"""Candle confirmation scores agree with clear directional OHLC structure."""

from app.signal_engine.chart_candles import aggregate_ohlc
from app.strategies.candle_confirm import (
    evaluate_candle_confirm,
    evaluate_candle_confirm_best,
    evaluate_candle_confirm_ohlc,
)


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


def test_candle_confirm_official_ohlc_path():
    ticks = _synthetic_up_ticks()
    official = {
        "1m": aggregate_ohlc(ticks, "1m", max_candles=80),
        "5m": aggregate_ohlc(ticks, "5m", max_candles=40),
        "15m": aggregate_ohlc(ticks, "15m", max_candles=24),
        "source": "deriv_official",
    }
    res = evaluate_candle_confirm_best(
        "upper", official=official, ticks=None, min_score=3.0, min_gap=0.5
    )
    assert res.confirmed is True
    assert res.details.get("source") == "deriv_official"
    assert "official" in res.explanation


def test_evaluate_ohlc_direct():
    ticks = _synthetic_up_ticks()
    res = evaluate_candle_confirm_ohlc(
        aggregate_ohlc(ticks, "1m", 80),
        aggregate_ohlc(ticks, "5m", 40),
        aggregate_ohlc(ticks, "15m", 24),
        "upper",
        min_score=3.0,
        min_gap=0.5,
        source="deriv_official",
    )
    assert res.score > 0


def test_r100_short_touch_uses_contract_specific_profile():
    ticks = _synthetic_up_ticks(n=9000)
    official = {
        "1m": aggregate_ohlc(ticks, "1m", max_candles=80),
        "5m": aggregate_ohlc(ticks, "5m", max_candles=40),
        "15m": aggregate_ohlc(ticks, "15m", max_candles=24),
        "source": "deriv_official",
    }
    result = evaluate_candle_confirm_best(
        "upper",
        official=official,
        symbol="R_100",
        barrier_distance=0.08,
        duration_seconds=540,
    )
    assert result.confirmed is True
    assert result.details["profile"] == "r100_touch_008_9m_v1"
    assert result.details["diagnostics"]["anchor_aligned"] is True
    assert result.details["diagnostics"]["trigger"] is True
    assert result.details["patterns_enabled"] == [
        "directional_impulse",
        "five_bar_breakout",
        "engulfing_support_only",
    ]


def test_r100_short_touch_rejects_opposite_anchor():
    ticks = _synthetic_down_ticks(n=9000)
    official = {
        "1m": aggregate_ohlc(ticks, "1m", max_candles=80),
        "5m": aggregate_ohlc(ticks, "5m", max_candles=40),
        "15m": aggregate_ohlc(ticks, "15m", max_candles=24),
        "source": "deriv_official",
    }
    result = evaluate_candle_confirm_best(
        "upper",
        official=official,
        symbol="R_100",
        barrier_distance=0.08,
        duration_seconds=540,
    )
    assert result.confirmed is False
    assert result.details["diagnostics"]["anchor_aligned"] is False
    assert "5m anchor not aligned" in result.explanation


def test_other_contract_keeps_generic_candle_path():
    ticks = _synthetic_up_ticks(n=9000)
    official = {
        "1m": aggregate_ohlc(ticks, "1m", max_candles=80),
        "5m": aggregate_ohlc(ticks, "5m", max_candles=40),
        "15m": aggregate_ohlc(ticks, "15m", max_candles=24),
        "source": "deriv_official",
    }
    result = evaluate_candle_confirm_best(
        "upper",
        official=official,
        symbol="R_100",
        barrier_distance=0.09,
        duration_seconds=540,
    )
    assert result.details.get("profile") is None
