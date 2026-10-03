from app.signal_engine.chart_candles import aggregate_ohlc


def test_aggregate_1m_ohlc():
    ticks = [
        (1000, 10.0),   # bucket 960
        (1020, 11.0),   # bucket 1020
        (1050, 9.5),
        (1065, 10.5),
        (1105, 12.0),   # bucket 1080
    ]
    candles = aggregate_ohlc(ticks, "1m", max_candles=10)
    assert len(candles) == 3
    assert candles[0]["epoch"] == 960
    assert candles[0]["open"] == 10.0
    assert candles[0]["close"] == 10.0
    assert candles[1]["epoch"] == 1020
    assert candles[1]["open"] == 11.0
    assert candles[1]["high"] == 11.0
    assert candles[1]["low"] == 9.5
    assert candles[1]["close"] == 10.5
    assert candles[2]["open"] == 12.0
    assert candles[2]["close"] == 12.0


def test_max_candles_trims_oldest():
    ticks = [(i * 60, float(i)) for i in range(20)]
    candles = aggregate_ohlc(ticks, "1m", max_candles=5)
    assert len(candles) == 5
    assert candles[0]["open"] == 15.0
