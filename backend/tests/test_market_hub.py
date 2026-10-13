from app.market_data.hub import MarketDataHub
from app.signal_engine.chart_candles import select_older_candles


def test_hub_merges_ticks_and_keeps_interval_candles():
    hub = MarketDataHub()
    hub.ingest_ticks("R_100", [(1_700_000_000 + index, 100 + index * 0.01) for index in range(120)])
    assert hub.snapshot("R_100")["last_tick"]["quote"] == 100 + 119 * 0.01
    first = hub.candles("R_100", "1m")
    assert len(first) >= 2
    hub.ingest_ticks("R_100", [(1_700_000_000 + 120, 102)])
    assert hub.snapshot("R_100")["last_tick"]["epoch"] == 1_700_000_000 + 120
    assert len(hub.candles("R_100", "1h")) >= 1


def test_official_candles_are_not_wiped_by_a_single_new_tick(monkeypatch):
    hub = MarketDataHub()
    candles = [
        {"epoch": 1_700_000_000 + index * 60, "open": 1, "high": 2, "low": 0.5, "close": 1.5}
        for index in range(30)
    ]
    hub.ingest_candles("R_100", "5m", candles, source="deriv_official")
    monkeypatch.setattr("app.market_data.hub.time.time", lambda: candles[-1]["epoch"] + 10)
    hub._updated[("R_100", "5m")] = candles[-1]["epoch"]
    hub.ingest_ticks("R_100", [(candles[-1]["epoch"] + 5, 9)])
    stored = hub.candles("R_100", "5m")
    assert stored[-1]["close"] == 1.5
    assert hub.snapshot("R_100")["last_tick"]["quote"] == 9


def test_older_candle_page_drops_a_wrapped_latest_response():
    older, wrapped = select_older_candles(
        [{"epoch": 100, "open": 1, "high": 1, "low": 1, "close": 1}, {"epoch": 160, "open": 1, "high": 2, "low": 1, "close": 2}],
        300,
    )
    assert wrapped is False
    assert [row["epoch"] for row in older] == [100, 160]
    empty, exhausted = select_older_candles(
        [{"epoch": 500, "open": 1, "high": 1, "low": 1, "close": 1}],
        300,
    )
    assert empty == []
    assert exhausted is True
