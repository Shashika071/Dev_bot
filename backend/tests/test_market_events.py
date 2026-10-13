from app.analysis.engine import _side_signals, analyze_candles
from app.analysis.market_events import price_action_events


def _bar(epoch: int, open_: float, high: float, low: float, close: float) -> dict:
    return {"epoch": epoch, "open": open_, "high": high, "low": low, "close": close}


def _uptrend_points() -> list[dict]:
    return [
        {"kind": "high", "index": 0, "epoch": 0, "price": 110},
        {"kind": "low", "index": 1, "epoch": 60, "price": 100},
        {"kind": "high", "index": 2, "epoch": 120, "price": 112},
        {"kind": "low", "index": 3, "epoch": 180, "price": 105},
    ]


def _names(events: list[dict]) -> set[tuple[str, str]]:
    return {(event["type"], event["direction"]) for event in events}


def test_bullish_break_of_structure_also_marks_continuation():
    candles = [
        _bar(0, 100, 110, 99, 105),
        _bar(60, 105, 106, 100, 104),
        _bar(120, 104, 112, 103, 108),
        _bar(180, 108, 109, 105, 107),
        _bar(240, 107, 111, 106, 111),
        _bar(300, 111, 114, 110, 113),
    ]
    events = price_action_events(candles, _uptrend_points(), atr=1.0, right=1)["events"]
    assert ("bos", "upper") in _names(events)
    assert ("trend_continuation", "upper") in _names(events)


def test_break_of_higher_low_is_choch_and_reversal():
    candles = [
        _bar(0, 100, 110, 99, 105),
        _bar(60, 105, 106, 100, 104),
        _bar(120, 104, 112, 103, 108),
        _bar(180, 108, 109, 105, 107),
        _bar(240, 107, 108, 106, 106),
        _bar(300, 106, 107, 103, 104),
    ]
    events = price_action_events(candles, _uptrend_points(), atr=1.0, right=1)["events"]
    assert ("choch", "lower") in _names(events)
    assert ("reversal", "lower") in _names(events)


def test_wick_through_high_that_closes_back_is_a_sweep():
    candles = [
        _bar(0, 100, 110, 99, 105),
        _bar(60, 105, 106, 100, 104),
        _bar(120, 104, 112, 103, 108),
        _bar(180, 108, 109, 105, 107),
        _bar(240, 107, 111, 106, 110),
        _bar(300, 110, 114, 109, 111),
    ]
    events = price_action_events(candles, _uptrend_points(), atr=1.0, right=1)["events"]
    assert ("liquidity_sweep", "lower") in _names(events)


def test_close_back_through_a_break_is_a_false_breakout():
    candles = [
        _bar(0, 100, 110, 99, 105),
        _bar(60, 105, 106, 100, 104),
        _bar(120, 104, 112, 103, 108),
        _bar(180, 108, 109, 105, 107),
        _bar(240, 107, 111, 106, 111),
        _bar(300, 111, 114, 110, 113),
        _bar(360, 113, 113.4, 110, 111),
    ]
    events = price_action_events(candles, _uptrend_points(), atr=1.0, right=1)["events"]
    assert ("false_breakout", "lower") in _names(events)


def test_wide_bar_is_a_spike():
    candles = [
        _bar(0, 100, 101, 99, 100),
        _bar(60, 100, 104, 99, 103),
    ]
    events = price_action_events(candles, [], atr=1.0, right=1)["events"]
    assert any(event["type"] == "spike" for event in events)


def test_bullish_break_is_a_buy_marker():
    candles = [
        _bar(0, 100, 110, 99, 105),
        _bar(60, 105, 106, 100, 104),
        _bar(120, 104, 112, 103, 108),
        _bar(180, 108, 109, 105, 107),
        _bar(240, 107, 111, 106, 111),
        _bar(300, 111, 114, 110, 113),
    ]
    events = price_action_events(candles, _uptrend_points(), atr=1.0, right=1)["events"]
    buys, sells = _side_signals(events, None)
    assert buys
    assert sells == []
    assert all(item["label"] == "BUY" and item["direction"] == "upper" for item in buys)


def test_bearish_break_is_a_sell_marker():
    candles = [
        _bar(0, 108, 112, 104, 106),
        _bar(60, 106, 107, 105, 106),
        _bar(120, 106, 110, 104, 105),
        _bar(180, 105, 106, 100, 101),
        _bar(240, 101, 102, 100.2, 101),
        _bar(300, 101, 101.5, 98, 99),
    ]
    points = [
        {"kind": "high", "index": 0, "epoch": 0, "price": 112},
        {"kind": "low", "index": 1, "epoch": 60, "price": 105},
        {"kind": "high", "index": 2, "epoch": 120, "price": 110},
        {"kind": "low", "index": 3, "epoch": 180, "price": 100},
    ]
    events = price_action_events(candles, points, atr=1.0, right=1)["events"]
    buys, sells = _side_signals(events, None)
    assert sells
    assert all(item["label"] == "SELL" and item["direction"] == "lower" for item in sells)
    assert buys == []


def test_snapshot_exposes_ema_trend_volatility_and_checklist():
    candles = []
    for index in range(80):
        price = 100 + index * 0.2
        candles.append(_bar(1_700_000_000 + index * 60, price - 0.05, price + 0.1, price - 0.1, price))
    snapshot = analyze_candles(
        {"1m": candles, "5m": candles, "15m": candles, "1h": candles},
        symbol="R_100",
        interval="1m",
        now=candles[-1]["epoch"] + 120,
        fresh=True,
    )
    assert snapshot["indicators"]["ema20"] is not None
    assert snapshot["indicators"]["ema50"] is not None
    assert "buy_signals" in snapshot
    assert "sell_signals" in snapshot
    assert snapshot["trend"]["label"] in {"Bullish", "Bearish", "Sideways"}
    assert snapshot["volatility"]["atr"]
    names = [item["name"] for item in snapshot["checklist"]]
    assert names == [
        "Trend",
        "Market structure",
        "Break of structure",
        "Change of character",
        "False breakout",
        "Liquidity sweep",
        "Momentum",
        "Higher timeframe",
        "Volatility",
        "Noise filter",
        "Pullback or continuation",
        "Data fresh",
    ]
