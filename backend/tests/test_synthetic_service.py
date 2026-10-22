"""Orchestration tests use fake public data and fake execution only."""
import pytest

from app.synthetic import service
from app.synthetic.policy import validate_config
from app.synthetic.store import SyntheticStore

NOW = 1_790_000_000


def complete(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as done:
        return done.value
    finally:
        coroutine.close()
    raise AssertionError("Unexpected external I/O")


@pytest.fixture
def harness(monkeypatch, tmp_path):
    store = SyntheticStore(str(tmp_path / "synthetic.sqlite"))
    scanner = service.SyntheticService(store)
    clock = [NOW]
    buys = []
    prediction = {"hybrid_passed": True, "p_rise": 0.65, "p_fall": 0.35}
    analysis = {"symbol": "R_100", "direction": "RISE", "eligible": True,
                "blockers": [], "decision_epoch": NOW}

    class Broker:
        async def connect(self):
            pass

        async def disconnect(self):
            pass

        async def get_active_symbols(self):
            return {"active_symbols": [{"symbol": "R_100", "exchange_is_open": 1}]}

        async def get_contracts_for(self, symbol):
            assert symbol == "R_100"
            return {"contracts_for": {"available": [
                {"contract_type": side, "barriers": 0, "min_contract_duration": "1m",
                 "max_contract_duration": "1d"} for side in ("CALL", "PUT")]}}

        async def get_candles(self, symbol, *, granularity, count):
            return [{"epoch": NOW - granularity}, {"epoch": NOW}]

    def analyze(frames, *, now):
        assert all(row["epoch"] < NOW for rows in frames.values() for row in rows)
        return dict(analysis)

    async def buy(signal, *, stake):
        assert store.signals()[0]["execution_status"] == "submitting"
        assert signal["_can_execute"]()
        buys.append(signal)
        return {"ok": True, "status": "bought", "contract_id": 1,
                "estimated_expiry_epoch": clock[0] + 300}

    monkeypatch.setattr(service, "DerivWSClient", Broker)
    monkeypatch.setattr(scanner, "_with_training_history", lambda frames, now: frames)
    monkeypatch.setattr(service, "analyze_synthetic", analyze)
    monkeypatch.setattr(service, "predict_frames", lambda *args, **kw: dict(prediction))
    monkeypatch.setattr(service, "execute_demo", buy)
    monkeypatch.setattr(service, "load_trade_prefs", lambda: {"daily_loss_limit": 0})
    monkeypatch.setattr(service, "execution_status", lambda: {"allowed": True})
    monkeypatch.setattr(service.settings, "bot_mode", "synthetic")
    monkeypatch.setattr(service.time, "time", lambda: clock[0])
    return scanner, store, prediction, analysis, clock, buys


def test_stopped_scan_never_issues_or_buys(harness):
    scanner, store, _, _, _, buys = harness
    complete(scanner._scan())
    assert store.state()["analyses"][0]["direction"] == "RISE"
    assert store.signals() == buys == []


def test_explicit_preview_cannot_buy_while_running(harness):
    scanner, store, _, _, _, buys = harness
    store.update_state(running=True, config={"demo_auto_trade": True})
    complete(scanner._scan(execute=False))
    assert store.signals() == buys == []


def test_default_start_records_signals_without_enabling_buys(harness):
    scanner, store, _, _, _, buys = harness
    store.update_state(running=True)
    complete(scanner._scan())
    assert store.signals()[0]["execution_status"] == "signal_only"
    assert buys == []


def test_demo_reservation_and_duplicate_prevention(harness):
    scanner, store, _, _, _, buys = harness
    store.update_state(running=True, config={"demo_auto_trade": True})
    complete(scanner._scan())
    complete(scanner._scan())
    assert len(buys) == 1
    assert store.signals()[0]["execution_status"] == "bought"
    assert store.signals()[0]["duration_seconds"] == 60


@pytest.mark.parametrize("ml", [
    {"hybrid_passed": False, "reason": "No promoted model"},
    {"hybrid_passed": None, "reason": "Incompatible artifact"},
])
def test_no_model_or_disagreement_fails_closed(harness, ml):
    scanner, store, prediction, _, _, buys = harness
    prediction.update(ml)
    store.update_state(running=True, config={"demo_auto_trade": True})
    complete(scanner._scan())
    assert store.signals() == buys == []
    assert store.state()["analyses"][0]["direction"] == "NO TRADE"


def test_old_signal_and_stop_after_quote_cannot_buy(harness, monkeypatch):
    scanner, store, _, _, clock, buys = harness
    store.update_state(running=True, config={"demo_auto_trade": True})

    async def delayed(signal, *, stake):
        clock[0] += 61
        assert not signal["_can_execute"]()
        clock[0] -= 61
        store.update_state(running=False)
        assert not signal["_can_execute"]()
        return {"ok": False, "status": "blocked"}

    monkeypatch.setattr(service, "execute_demo", delayed)
    complete(scanner._scan())
    assert buys == []
    assert store.signals()[0]["execution_status"] == "blocked"


def test_missing_indicators_or_closed_bar_blocks(harness):
    scanner, store, _, analysis, _, buys = harness
    analysis.update(direction="NO TRADE", eligible=False, blockers=["Indicators disagree"])
    store.update_state(running=True, config={"demo_auto_trade": True})
    complete(scanner._scan())
    assert store.signals() == buys == []


def test_durable_cap_and_unknown_purchase(tmp_path):
    path = str(tmp_path / "synthetic.sqlite")
    store = SyntheticStore(path)
    store.update_state(running=True, config={"max_signals_per_day": 1, "demo_auto_trade": True})
    signal = {"symbol": "R_100", "direction": "RISE", "created_epoch": NOW,
              "expiry_epoch": NOW + 300, "setup_key": "one"}
    row, reason = store.reserve(signal, NOW)
    assert row and reason is None
    assert SyntheticStore(path).reserve({**signal, "setup_key": "two"}, NOW + 301)[0] is None
    store.finish(row["id"], {"status": "unknown"})
    assert SyntheticStore(path).reserve({**signal, "setup_key": "tomorrow"}, NOW + 86400)[0] is None


@pytest.mark.parametrize("updates", [
    {"symbols": ["1HZ100V"]}, {"symbols": ["frxEURUSD"]}, {"duration_seconds": 14400},
    {"max_signals_per_day": 0}, {"stake": float("nan")}, {"demo_auto_trade": "yes"},
])
def test_fixed_product_validation(updates):
    with pytest.raises(ValueError):
        validate_config(updates)


def test_saved_five_minute_expiry_is_reset_to_one_minute():
    config = validate_config({}, {"duration_seconds": 300, "stake": 2.0, "max_signals_per_day": 8, "demo_auto_trade": False})
    assert config["duration_seconds"] == 60
    assert config["stake"] == 2.0
    assert config["max_signals_per_day"] == 8


def test_chart_keeps_the_forming_candle_and_allows_one_minute():
    closed = {"epoch": NOW - 120, "open": 1, "high": 2, "low": 1, "close": 1.5}
    forming = {"epoch": NOW - 30, "open": 1.5, "high": 1.8, "low": 1.4, "close": 1.7}
    rows = service._chart_candles([closed, forming], 60, NOW)
    assert [row["epoch"] for row in rows] == [NOW - 120, NOW - 30]
    assert "1m" in service.CHART_TIMEFRAMES and service.CHART_TIMEFRAMES["1m"] == 60


def test_quote_reports_the_latest_tick_without_opening_a_socket(monkeypatch):
    scanner = service.SyntheticService()
    monkeypatch.setattr(scanner, "_ensure_quote", lambda: None)
    scanner._quote = {"symbol": "R_100", "epoch": NOW - 1, "quote": 123.45}
    body = scanner.quote()
    assert body["quote"] == 123.45
    assert body["age_seconds"] >= 1


def test_chart_confidence_uses_the_latest_scan_without_opening_a_socket(monkeypatch, tmp_path):
    store = SyntheticStore(str(tmp_path / "synthetic.sqlite"))
    store.update_state(analyses=[{
        "decision_epoch": NOW,
        "ml": {
            "direction": "FALL", "p_rise": 0.22, "p_fall": 0.71, "p_tie": 0.07,
            "confidence": 0.71, "threshold": 0.6, "reason": "model read",
        },
    }])
    scanner = service.SyntheticService(store)
    monkeypatch.setattr(scanner, "_ensure_confidence", lambda: None)
    body = scanner.confidence()
    assert body["direction"] == "FALL"
    assert body["p_rise"] == 0.22
    assert body["p_fall"] == 0.71
    assert body["decision_epoch"] == NOW


def test_confidence_view_drops_non_finite_probabilities():
    body = service.confidence_view({"ml": {"direction": "MAYBE", "p_rise": float("nan"), "p_fall": True}})
    assert body["direction"] == "NO TRADE"
    assert body["p_rise"] is None
    assert body["p_fall"] is None
