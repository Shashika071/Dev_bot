"""Orchestration integration tests with deterministic broker/data doubles."""
from datetime import datetime, timezone

import pytest

from app.forex import service, execution
from app.forex.store import ForexStore

NOW = int(datetime(2026, 10, 19, 10, tzinfo=timezone.utc).timestamp())


def complete(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as done:
        return done.value
    finally:
        coroutine.close()
    raise AssertionError("Unexpected real asynchronous I/O")


@pytest.fixture
def harness(monkeypatch, tmp_path):
    clock = [NOW]
    store = ForexStore(str(tmp_path / "forex.sqlite"))
    store.update_state(config={"symbols": ["frxGBPUSD", "frxUSDJPY"]})
    scanner = service.ForexService(store)
    buys = []
    context = {"source": "Test", "as_of": NOW, "coverage_start": NOW - 7200,
               "coverage_end": NOW + 86400, "currencies": ["GBP", "USD", "JPY"], "events": []}

    class Broker:
        async def connect(self):
            pass

        async def disconnect(self):
            pass

        async def get_active_symbols(self):
            return {"active_symbols": [{"market": "forex", "underlying_symbol": pair,
                                        "exchange_is_open": 1} for pair in store.state()["config"]["symbols"]]}

        async def get_contracts_for(self, symbol):
            return {"contracts_for": {"available": [{"contract_type": side, "barriers": 0} for side in ("CALL", "PUT")]}}

        async def get_candles(self, symbol, *, granularity, count):
            row = {"open": 1, "close": 1.001, "high": 1.002, "low": 0.999}
            return [{**row, "epoch": NOW - granularity}, {**row, "epoch": NOW}]

    async def gather(*coroutines):
        return [await coroutine for coroutine in coroutines]

    async def load_context(*args):
        return context

    def analysis(frames, *, symbol, now, duration_seconds):
        assert all(row["epoch"] < NOW for rows in frames.values() for row in rows)
        return {"symbol": symbol, "ready": True, "direction": "RISE", "score": 90 if symbol == "frxGBPUSD" else 85,
                "blockers": [], "reasons": ["test setup"], "factors": [],
                "as_of_epoch": frames["15m"][-1]["epoch"], "timeframes": {}}

    async def buy(signal, *, stake):
        if not signal["_can_execute"]():
            return {"ok": False, "status": "blocked"}
        buys.append(signal)
        return {"ok": True, "status": "bought", "contract_id": 123, "estimated_expiry_epoch": clock[0] + 14430}

    monkeypatch.setattr(service, "DerivWSClient", Broker)
    monkeypatch.setattr(service.asyncio, "gather", gather)
    monkeypatch.setattr(service.time, "time", lambda: clock[0])
    monkeypatch.setattr(service, "load_context", load_context)
    monkeypatch.setattr(service, "analyze_forex", analysis)
    monkeypatch.setattr(service, "execution_status", lambda: {"allowed": True})
    monkeypatch.setattr(service, "load_trade_prefs", lambda: {"daily_loss_limit": 0})
    monkeypatch.setattr(execution, "execute_demo", buy)
    return scanner, store, buys, context, clock


def test_stopped_scan_previews_but_never_issues_or_buys(harness):
    scanner, store, buys, _, _ = harness
    complete(scanner._scan())
    assert len(store.state()["analyses"]) == 2
    assert not store.signals() and not buys


def test_running_scan_ranks_pairs_and_reserves_before_buy(harness):
    scanner, store, buys, _, _ = harness
    store.update_state(running=True)
    complete(scanner._scan())
    assert len(buys) == 1 and buys[0]["symbol"] == "frxGBPUSD"
    assert store.signals()[0]["execution_status"] == "bought"
    assert store.signals()[0]["expiry_epoch"] == NOW + 14430
    complete(scanner._scan())
    assert len(buys) == 1


def test_news_during_holding_period_prevents_issue(harness):
    scanner, store, buys, context, _ = harness
    context["events"] = [{"currency": "USD", "title": "NFP", "at": NOW + 14000, "impact": "high"}]
    store.update_state(running=True)
    complete(scanner._scan())
    assert not store.signals() and not buys


def test_stop_during_auth_blocks_purchase(harness, monkeypatch):
    scanner, store, buys, _, _ = harness
    async def delayed_buy(signal, *, stake):
        assert store.signals()[0]["execution_status"] == "submitting"
        store.update_state(running=False)
        assert not signal["_can_execute"]()
        return {"ok": False, "status": "blocked"}
    monkeypatch.setattr(execution, "execute_demo", delayed_buy)
    store.update_state(running=True)
    complete(scanner._scan())
    assert not buys and store.signals()[0]["execution_status"] == "blocked"


def test_news_coverage_rechecked_after_auth_delay(harness, monkeypatch):
    scanner, store, buys, context, clock = harness
    context["coverage_end"] = NOW + 14400 + 1800 + 10
    async def delayed_buy(signal, *, stake):
        clock[0] += 20
        assert not signal["_can_execute"]()
        return {"ok": False, "status": "blocked"}
    monkeypatch.setattr(execution, "execute_demo", delayed_buy)
    store.update_state(running=True)
    complete(scanner._scan())
    assert not buys and store.signals()[0]["execution_status"] == "blocked"


def test_partial_candle_is_not_promoted_after_request_crosses_close(monkeypatch):
    now = [NOW + 59]
    class Broker:
        async def get_candles(self, *args, **kwargs):
            now[0] += 2
            return [{"epoch": NOW - 60}, {"epoch": NOW}]
    monkeypatch.setattr(service.time, "time", lambda: now[0])
    rows = complete(service.capture_candles(Broker(), "frxGBPUSD", "1m"))
    assert rows == [{"epoch": NOW - 60}]
