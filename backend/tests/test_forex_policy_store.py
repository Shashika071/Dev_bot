from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

from app.forex.policy import local_day, session_context, supports_rise_fall, validate_config
from app.forex.store import ForexStore


def stamp(text):
    return int(datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp())


def signal(now, key="setup"):
    return {"symbol": "frxEURUSD", "direction": "RISE", "created_epoch": now,
            "expiry_epoch": now + 14400, "setup_key": key}


def test_policy_rejects_non_four_hour_expiries_synthetics_and_nonfinite_stake():
    for updates in ({"duration_seconds": 300}, {"duration_seconds": 3600}, {"duration_seconds": 86400},
                    {"stake": float("nan")}, {"stake": 11}, {"symbols": ["R_100"]}, {"symbols": []}):
        with pytest.raises(ValueError):
            validate_config(updates)
    config = validate_config({"max_signals_per_day": 20, "duration_seconds": 14400})
    assert config["max_signals_per_day"] == 2
    assert config["duration_seconds"] == 14400
    assert config["cooldown_seconds"] == 14400
    # Legacy saved hour counts are coerced to the locked four-hour product.
    restored = validate_config({}, {"duration_seconds": 86400, "symbols": ["frxEURUSD"], "stake": 1.0})
    assert restored["duration_seconds"] == 14400


def test_requires_zero_barrier_both_directions_and_accepted_duration():
    contracts = [{"contract_type": kind, "barriers": 0, "min_contract_duration": "15m", "max_contract_duration": "1d"} for kind in ("CALL", "PUT")]
    assert supports_rise_fall(contracts, 14400)
    assert not supports_rise_fall(contracts[:1], 14400)
    assert not supports_rise_fall([{**row, "barriers": 1} for row in contracts], 14400)
    assert not supports_rise_fall(contracts, 172800)
    assert not supports_rise_fall([{**row, "start_type": "forward"} for row in contracts], 14400)


def test_sessions_dst_and_holding_period_weekend():
    assert not session_context(stamp("2026-10-19T08:00:00"), 14400)["blockers"]
    assert session_context(stamp("2026-10-23T18:00:00"), 14400)["blockers"]
    assert session_context(stamp("2026-10-24T08:00:00"), 14400)["blockers"]
    # London 08:00 during winter is 08 UTC, during summer 07 UTC.
    winter = session_context(stamp("2026-01-19T07:30:00"), 3600)
    summer = session_context(stamp("2026-07-20T07:30:00"), 3600)
    assert "London session" not in winter["active"]
    assert "London session" in summer["active"]


def test_colombo_midnight():
    assert local_day(stamp("2026-10-19T18:29:59")) == "2026-10-19"
    assert local_day(stamp("2026-10-19T18:30:00")) == "2026-10-20"


def test_durable_cap_across_symbols_and_restarts(tmp_path):
    path = str(tmp_path / "forex.sqlite")
    store = ForexStore(path)
    store.update_state(running=True, config={"demo_auto_trade": False})
    now = stamp("2026-10-19T00:00:00")
    assert store.reserve(signal(now, "a"), now)[0]
    assert ForexStore(path).reserve(signal(now + 14400, "b"), now + 14400)[0]
    row, reason = store.reserve(signal(now + 28800, "c"), now + 28800)
    assert row is None and "cap" in reason
    assert store.daily_count(now) == 2


def test_concurrent_reservations_only_one_and_no_overlap_across_midnight(tmp_path):
    path = str(tmp_path / "forex.sqlite")
    store = ForexStore(path)
    store.update_state(running=True, config={"demo_auto_trade": False})
    now = stamp("2026-10-19T18:00:00")
    def reserve(i):
        return ForexStore(path).reserve(signal(now, f"pair-{i}"), now)[0]
    with ThreadPoolExecutor(max_workers=8) as executor:
        assert sum(row is not None for row in executor.map(reserve, range(8))) == 1
    assert store.daily_count(now + 3600) == 0
    assert store.reserve(signal(now + 3600, "new-day"), now + 3600)[0] is None


def test_unknown_or_interrupted_purchase_never_retried(tmp_path):
    store = ForexStore(str(tmp_path / "forex.sqlite"))
    store.update_state(running=True)
    now = stamp("2026-10-19T00:00:00")
    row, _ = store.reserve(signal(now), now)
    assert row["execution_status"] == "submitting"
    assert store.reserve(signal(now + 86400, "later"), now + 86400)[0] is None
    store.finish(row["id"], {"ok": False, "status": "unknown"})
    assert store.reserve(signal(now + 172800, "much-later"), now + 172800)[0] is None


def test_scan_lease_and_stop_are_durable(tmp_path):
    path = str(tmp_path / "forex.sqlite")
    one, two = ForexStore(path), ForexStore(path)
    owner = one.acquire(1000)
    assert owner and two.acquire(1001) is None
    two.release("someone-else")
    assert two.acquire(1002) is None
    one.release(owner)
    assert two.acquire(1002)
    one.update_state(running=False)
    assert two.reserve(signal(1003), 1003)[1] == "Scanner stopped"


def test_reservation_rejects_config_change_before_any_attempt(tmp_path):
    store = ForexStore(str(tmp_path / "forex.sqlite"))
    previous = store.update_state(running=True, config={"demo_auto_trade": False})["config"]
    store.update_state(config={"demo_auto_trade": True})
    row, reason = store.reserve(signal(1000), 1000, expected_config=previous)
    assert row is None and "Configuration changed" in reason
    assert store.signals() == []


def test_committed_stakes_bound_maximum_daily_loss_without_settlement(tmp_path):
    store = ForexStore(str(tmp_path / "forex.sqlite"))
    store.update_state(running=True)
    now = stamp("2026-10-19T00:00:00")
    row, _ = store.reserve({**signal(now), "stake": 1}, now, stake_budget=1)
    store.finish(row["id"], {"ok": True, "status": "bought"})
    next_row, reason = store.reserve(signal(now + 14400, "later"), now + 14400, stake_budget=1)
    assert next_row is None and "stake budget" in reason


def test_purchase_delay_extends_overlap_guard_in_database(tmp_path):
    store = ForexStore(str(tmp_path / "forex.sqlite"))
    store.update_state(running=True)
    now = stamp("2026-10-19T00:00:00")
    row, _ = store.reserve(signal(now), now)
    store.finish(row["id"], {"ok": True, "status": "bought", "estimated_expiry_epoch": now + 14520})
    assert store.signals()[0]["expiry_epoch"] == now + 14520
    assert store.reserve(signal(now + 14400, "later"), now + 14400)[0] is None
