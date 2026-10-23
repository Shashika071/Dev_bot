"""Orchestration tests use fake public data and fake execution only."""
import asyncio
import threading

import pytest

from app.synthetic import service
from app.synthetic.policy import validate_config
from app.synthetic.store import SyntheticStore

NOW = 1_790_000_000


def complete(coroutine):
    return asyncio.run(coroutine)


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
    monkeypatch.setattr(service, "execute_demo", buy)
    monkeypatch.setattr(service, "load_trade_prefs", lambda: {"daily_loss_limit": 0})
    monkeypatch.setattr(service, "execution_status", lambda: {"allowed": True})
    monkeypatch.setattr(service.settings, "bot_mode", "synthetic")
    monkeypatch.setattr(service.time, "time", lambda: clock[0])
    return scanner, store, prediction, analysis, clock, buys


def test_chart_offers_the_research_timeframes():
    from app.synthetic.service import CHART_TIMEFRAMES
    assert CHART_TIMEFRAMES == {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "1d": 86400}


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


def test_previous_model_does_not_block_a_technical_signal(harness):
    scanner, store, prediction, _, _, buys = harness
    prediction.update(hybrid_passed=False, reason="No promoted model")
    store.update_state(running=True, config={"demo_auto_trade": True})
    complete(scanner._scan())
    assert store.signals()[0]["direction"] == "RISE"
    assert buys[0]["direction"] == "RISE"
    assert "model" not in " ".join(store.state()["analyses"][0]["blockers"]).lower()


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
    current = NOW // 60 * 60
    closed = {"epoch": current - 60, "open": 1, "high": 2, "low": 1, "close": 1.5}
    forming = {"epoch": current, "open": 1.5, "high": 1.8, "low": 1.4, "close": 1.7}
    rows = service._chart_candles([closed, forming], 60, NOW)
    assert [row["epoch"] for row in rows] == [current - 60, current]
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
        "ml": {"direction": "FALL", "p_rise": 0.22, "p_fall": 0.71, "confidence": 0.71},
        "indicators": {"frama": {"direction": "FALL"}, "range_filter": {"direction": "FALL"}},
        "timeframes": {"5m": {"trend": "FALL"}, "15m": {"trend": "FALL"}},
        "features": {"momentum": -1},
        "factors": [{"id": "fresh_signal", "status": "PASS"}],
    }])
    scanner = service.SyntheticService(store)
    monkeypatch.setattr(scanner, "_ensure_confidence", lambda: None)
    body = scanner.confidence()
    assert body["direction"] == "FALL"
    assert body["p_rise"] is None
    assert body["p_fall"] is None
    assert body["combined_p_fall"] == pytest.approx(1)
    assert body["decision_epoch"] == NOW


def test_confidence_uses_the_five_signals_and_ignores_the_model():
    agreed = service.confidence_view({
        "ml": {"p_rise": 0.72, "p_fall": 0.2, "confidence": 0.72, "direction": "RISE", "promoted": True},
        "indicators": {"frama": {"direction": "RISE"}, "range_filter": {"direction": "RISE"}},
        "timeframes": {"5m": {"trend": "RISE"}, "15m": {"trend": "RISE"}},
        "features": {"momentum": 1},
        "factors": [{"id": "fresh_signal", "status": "PASS"}],
    })
    assert agreed["direction"] == "RISE"
    assert agreed["p_rise"] is None
    assert agreed["combined_p_rise"] == pytest.approx(1)
    assert agreed["combined_p_fall"] == pytest.approx(0)
    assert all(item["id"] != "model" for item in agreed["checks"])

    split = service.confidence_view({
        "ml": {"p_rise": 0.9, "p_fall": 0.1, "direction": "RISE"},
        "indicators": {"frama": {"direction": "RISE"}, "range_filter": {"direction": "FALL"}},
        "timeframes": {"5m": {"trend": "RISE"}, "15m": {"trend": "FALL"}},
        "features": {"momentum": 1},
        "factors": [{"id": "fresh_signal", "status": "PASS"}],
    })
    assert split["direction"] == "NO TRADE"
    assert split["combined_p_rise"] == pytest.approx(3 / 5)
    assert split["combined_p_fall"] == pytest.approx(2 / 5)
    assert "Not a trade" in split["reason"]

    incomplete = service.confidence_view({
        "indicators": {"frama": {"direction": "NEUTRAL"}, "range_filter": {"direction": "RISE"}},
        "timeframes": {"5m": {"trend": "RISE"}, "15m": {"trend": "RISE"}},
        "features": {"momentum": 0},
        "factors": [{"id": "fresh_signal", "status": "BLOCK"}],
    })
    assert incomplete["direction"] == "NO TRADE"
    assert incomplete["combined_p_rise"] == pytest.approx(3 / 5)
    assert "FRAMA" in incomplete["reason"]
    assert "Momentum" in incomplete["reason"]


def test_confidence_view_drops_non_finite_probabilities():
    body = service.confidence_view({"ml": {"direction": "MAYBE", "p_rise": float("nan"), "p_fall": True}})
    assert body["direction"] == "NO TRADE"
    assert body["p_rise"] is None
    assert body["p_fall"] is None


def test_chart_rejects_future_malformed_and_unaligned_candles():
    current = NOW // 60 * 60
    bar = {"epoch": current, "open": 1, "high": 2, "low": 1, "close": 1.5}
    assert service._chart_candles([
        {**bar, "epoch": current + 60}, {**bar, "epoch": current + 1},
        {**bar, "close": float("nan")}, {**bar, "close": 3}, bar,
    ], 60, NOW) == [bar]


@pytest.mark.parametrize("tick", [
    {"epoch": NOW, "quote": float("nan")},
    {"epoch": NOW, "quote": float("inf")},
    {"epoch": float("inf"), "quote": 10},
    {"epoch": True, "quote": 10},
    {"epoch": NOW, "quote": True},
    {"epoch": NOW, "quote": -1},
    {"epoch": NOW - 20, "quote": 10},
    {"epoch": NOW + 10, "quote": 10},
    {"epoch": NOW - 2, "quote": 10},
    {"epoch": NOW, "quote": 10, "symbol": "R_50"},
])
def test_tick_feed_rejects_invalid_stale_and_out_of_order_updates(monkeypatch, tick):
    scanner = service.SyntheticService()
    monkeypatch.setattr(service.time, "time", lambda: NOW)
    scanner._accept_tick({"tick": {"symbol": "R_100", "epoch": NOW - 1, "quote": 123.45}})
    scanner._accept_tick({"tick": tick})
    assert scanner._quote["epoch"] == NOW - 1
    assert scanner._quote["quote"] == 123.45


def test_tick_feed_keeps_same_second_price_and_small_clock_skew(monkeypatch):
    scanner = service.SyntheticService()
    monkeypatch.setattr(service.time, "time", lambda: NOW)
    scanner._accept_tick({"tick": {"symbol": "R_100", "epoch": NOW - 1, "quote": 10}})
    scanner._accept_tick({"tick": {"symbol": "R_100", "epoch": NOW - 1, "quote": 11}})
    assert scanner._quote["epoch"] == NOW - 1
    assert scanner._quote["quote"] == 11
    scanner._accept_tick({"tick": {"symbol": "R_100", "epoch": NOW + 1, "quote": 12}})
    assert scanner._quote["epoch"] == NOW + 1
    assert scanner._quote["quote"] == 12


def test_quote_reports_staleness_and_recovers_on_a_fresh_tick(monkeypatch):
    scanner = service.SyntheticService()
    clock = [NOW]
    monkeypatch.setattr(scanner, "_ensure_quote", lambda: None)
    monkeypatch.setattr(service.time, "time", lambda: clock[0])
    assert scanner.quote()["status"] == "connecting"
    scanner._accept_tick({"tick": {"epoch": NOW, "quote": 12}})
    assert scanner.quote()["status"] == "live"
    clock[0] += 20
    assert scanner.quote()["stale"] is True
    assert scanner.quote()["status"] == "stale"
    scanner._quote_error = "reconnecting"
    scanner._accept_tick({"tick": {"epoch": clock[0], "quote": 13}})
    assert scanner.quote()["status"] == "live"
    assert scanner.quote()["error"] is None


def test_silent_quote_subscription_reconnects(monkeypatch):
    scanner = service.SyntheticService()
    clients = []
    monotonic = [0]

    class SilentBroker:
        connected = True

        def __init__(self):
            self.disconnected = False
            clients.append(self)

        async def connect(self):
            if len(clients) == 2:
                raise asyncio.CancelledError

        async def subscribe_ticks(self, symbol, handler):
            assert symbol == "R_100"

        async def disconnect(self):
            self.disconnected = True

    async def advance(seconds):
        monotonic[0] += seconds

    monkeypatch.setattr(service, "DerivWSClient", SilentBroker)
    monkeypatch.setattr(service.time, "monotonic", lambda: monotonic[0])
    monkeypatch.setattr(service.asyncio, "sleep", advance)
    with pytest.raises(asyncio.CancelledError):
        complete(scanner._quote_loop())
    assert len(clients) == 2
    assert all(client.disconnected for client in clients)
    assert "TimeoutError" in scanner._quote_error


def test_cached_confidence_keeps_original_time_and_marks_old_decision_stale(harness, monkeypatch):
    scanner, store, _, _, clock, _ = harness
    monkeypatch.setattr(scanner, "_ensure_confidence", lambda: None)
    store.update_state(last_scan=NOW, analyses=[{
        "decision_epoch": NOW - 120,
        "indicators": {"frama": {"direction": "RISE"}, "range_filter": {"direction": "RISE"}},
        "timeframes": {"5m": {"trend": "RISE"}, "15m": {"trend": "RISE"}},
        "features": {"momentum": 1},
    }])
    first = scanner.confidence()
    clock[0] += 1
    second = scanner.confidence()
    assert first["updated_at"] == second["updated_at"] == NOW
    assert second["status"] == "stale"
    assert second["stale"] is True
    assert second["age_seconds"] == 1


def test_confidence_refresh_runs_in_a_worker_and_once_per_closed_candle(harness, monkeypatch):
    scanner, store, _, _, clock, buys = harness
    owner_thread = threading.get_ident()
    calls = []

    def analyze(frames, now):
        assert threading.get_ident() != owner_thread
        calls.append(now)
        return {
            "decision_epoch": now // 60 * 60,
            "indicators": {"frama": {"direction": "RISE"}, "range_filter": {"direction": "RISE"}},
            "timeframes": {"5m": {"trend": "RISE"}, "15m": {"trend": "RISE"}},
            "features": {"momentum": 1},
        }

    monkeypatch.setattr(scanner, "_analyze_frames", analyze)
    complete(scanner._refresh_confidence())
    complete(scanner._refresh_confidence())
    assert len(calls) == 1
    assert scanner._confidence_snapshot()["status"] == "ready"
    assert store.state().get("analyses") is None
    assert buys == []
    clock[0] += 60
    complete(scanner._refresh_confidence())
    assert len(calls) == 2


def test_blank_scan_does_not_hide_model_confidence(harness, monkeypatch):
    scanner, store, _, _, clock, _ = harness
    store.update_state(last_scan=clock[0], analyses=[{
        "decision_epoch": clock[0] // 60 * 60,
        "ml": {"direction": "NO TRADE", "p_rise": None, "p_fall": None,
               "reason": "Latest candidate did not pass validation; no model is promoted"},
    }])

    def analyze(frames, now):
        return {"decision_epoch": now // 60 * 60,
                "ml": {"p_rise": 0.48, "p_fall": 0.47, "promoted": False, "direction": "NO TRADE"},
                "indicators": {"frama": {"direction": "RISE"}, "range_filter": {"direction": "RISE"}},
                "timeframes": {"5m": {"trend": "RISE"}, "15m": {"trend": "RISE"}},
                "features": {"momentum": 1}}

    monkeypatch.setattr(scanner, "_analyze_frames", analyze)
    complete(scanner._refresh_confidence())
    body = scanner._confidence_snapshot()
    assert body["p_rise"] is None
    assert body["p_fall"] is None
    assert body["combined_p_rise"] == pytest.approx(1)
    assert body["status"] == "ready"


def test_slow_inference_cannot_use_an_expired_entry_window(harness, monkeypatch):
    scanner, store, _, analysis, clock, buys = harness
    store.update_state(running=True, config={"demo_auto_trade": True})

    def analyze(frames, *, now):
        clock[0] += 25
        return dict(analysis)

    monkeypatch.setattr(service, "analyze_synthetic", analyze)
    complete(scanner._scan())
    assert buys == store.signals() == []
    assert "entry window" in " ".join(store.state()["analyses"][0]["blockers"])


def test_stored_model_blocker_is_not_shown(harness):
    scanner, store, _, _, _, _ = harness
    store.update_state(analyses=[{
        "direction": "NO TRADE",
        "technical_direction": "RISE",
        "eligible": False,
        "blockers": ["A validated CatBoost model must agree", "Volatility 100 is unavailable or suspended"],
        "ml": {"reason": "Latest candidate did not pass validation"},
    }])
    shown = scanner.status()["analyses"][0]
    assert "ml" not in shown
    assert shown["blockers"] == ["Volatility 100 is unavailable or suspended"]
    assert shown["direction"] == "NO TRADE"
