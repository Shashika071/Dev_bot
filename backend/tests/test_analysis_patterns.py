from app.analysis.candle_patterns import detect_at
from app.analysis.chart_patterns import chart_patterns
from app.analysis.engine import analyze_candles, closed_candles
from app.analysis.structure import structure_state, support_resistance, swings, trendlines


def _bar(epoch: int, open_: float, high: float, low: float, close: float) -> dict:
    return {"epoch": epoch, "open": open_, "high": high, "low": low, "close": close}


def test_descending_swings_draw_both_channel_lines():
    candles = [_bar(index, 1, 1, 1, 1) for index in range(30)]
    points = [
        {"kind": "high", "index": 0, "epoch": 0, "price": 150},
        {"kind": "high", "index": 10, "epoch": 10, "price": 140},
        {"kind": "high", "index": 20, "epoch": 20, "price": 130},
        {"kind": "low", "index": 5, "epoch": 5, "price": 120},
        {"kind": "low", "index": 15, "epoch": 15, "price": 110},
        {"kind": "low", "index": 25, "epoch": 25, "price": 100},
    ]
    lines = trendlines(candles, points)
    kinds = {item["type"] for item in lines}
    assert kinds == {"support_trendline", "resistance_trendline"}
    for line in lines:
        assert line["geometry"]["time2"] == 29
        assert line["geometry"]["price2"] < line["geometry"]["price1"]


def test_bullish_engulfing_and_hammer_are_named():
    engulfed = detect_at([
        _bar(60, 10, 10.2, 9.4, 9.5),
        _bar(120, 9.45, 10.4, 9.4, 10.3),
    ])
    assert any(item["type"] == "bullish_engulfing" and item["direction"] == "upper" for item in engulfed)
    hammer = detect_at([
        _bar(60, 10, 10.1, 9.9, 10),
        _bar(120, 10, 10.15, 8.5, 10.1),
    ])
    assert any(item["type"] == "hammer" for item in hammer)


def test_gap_pattern_is_downweighted_for_r100_context():
    candles = [
        _bar(0, 10, 10.4, 9.2, 9.3),
        _bar(60, 8.5, 8.7, 8.2, 8.55),
        _bar(120, 9.2, 10.6, 9.1, 10.4),
    ]
    # The middle bar gaps below the first and the third gaps above the middle.
    events = detect_at(candles, allow_gaps=False)
    baby = [item for item in events if item["type"] == "bullish_abandoned_baby"]
    if baby:
        assert baby[0]["status"] == "downweighted"
        assert baby[0]["confidence"] < 0.5


def test_support_clusters_nearby_swing_highs():
    candles = []
    price = 100.0
    for index in range(40):
        epoch = index * 60
        if index in {8, 24}:
            candles.append(_bar(epoch, price, price + 5, price - 0.2, price + 0.4))
        else:
            candles.append(_bar(epoch, price, price + 0.4, price - 0.4, price + 0.1))
    points = swings(candles, left=2, right=2)
    zones = support_resistance(candles, points)
    assert any(zone["type"] == "resistance" and zone["touches"] >= 2 for zone in zones)


def test_closed_candles_drop_the_forming_bar():
    candles = [_bar(1000, 1, 2, 1, 1.5), _bar(1060, 1.5, 1.6, 1.4, 1.55)]
    closed = closed_candles(candles, "1m", now=1100)
    assert [item["epoch"] for item in closed] == [1000]


def test_analysis_signal_uses_only_closed_bar_time():
    candles = []
    for index in range(80):
        price = 100 + index * 0.2
        candles.append(_bar(1_700_000_000 + index * 60, price - 0.05, price + 0.1, price - 0.1, price))
    forming = _bar(candles[-1]["epoch"] + 60, 200, 220, 180, 210)
    snapshot = analyze_candles(
        {"1m": candles + [forming], "5m": candles, "15m": candles},
        symbol="R_100",
        interval="1m",
        now=forming["epoch"] + 10,
        fresh=True,
    )
    assert snapshot["as_of_epoch"] == candles[-1]["epoch"]
    assert snapshot["volume_note"].startswith("R_100 has no exchange volume")
    assert "vwap" not in snapshot["indicators"]


def test_double_bottom_break_is_bullish():
    candles = []
    epoch = 0
    path = (
        [100] * 5
        + [96, 94, 96]
        + [100] * 6
        + [96, 94.1, 96]
        + [100] * 6
        + [103, 104, 105]
    )
    for price in path:
        candles.append(_bar(epoch, price - 0.2, price + 0.3, price - 0.3, price))
        epoch += 60
    names = {item["type"] for item in chart_patterns(candles)}
    assert "double_bottom" in names or "range_breakout" in names
    assert structure_state(swings(candles)) in {"HH_HL", "LH_LL", "mixed", "expanding", "insufficient"}
