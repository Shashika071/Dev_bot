"""Digit Matches research tests. Synthetic ticks are not trading evidence."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from app.digitmatch.account import AccountRejected, AuthFamilyRejected, assert_classic_token, verify_demo_authorize
from app.digitmatch.api_redaction_probe import probe_summary
from app.digitmatch.calibration import apply_temperature, fit_temperature
from app.digitmatch.digits import extract_last_digit
from app.digitmatch.ev import break_even_probability, estimated_net_ev, net_profit
from app.digitmatch.execution import CycleState, apply_settlement, reconcile_unknown, run_cycle
from app.digitmatch.fake_broker import FakeBroker
from app.digitmatch.features import build_examples
from app.digitmatch.ingestion import normalize_observation
from app.digitmatch.instrument import MarketUnavailable, resolve_volatility_100, validate_digitmatch_five_ticks
from app.digitmatch.messages import buy_request, proposal_request
from app.digitmatch.modeling import fit_mlp
from app.digitmatch.probability import as_simplex
from app.digitmatch.redaction import redact, safe_message_summary
from app.digitmatch.risk import RiskSnapshot, assess
from app.digitmatch.splits import chronological_split
from app.digitmatch.state import resolve_ui_state
from app.digitmatch.store import SqlStore
from app.digitmatch.training import run_training, select_best
from app.models.digitmatch import DmContract


def _risk(**overrides) -> RiskSnapshot:
    base = dict(
        timezone_name="Asia/Colombo",
        stake=1,
        cooldown_seconds=30,
        max_trades_per_day=50,
        daily_loss_limit=20,
        daily_profit_stop=20,
        max_open_contracts=1,
        open_contracts=0,
        trades_today=0,
        pnl_today=0,
        last_settled_at=None,
        emergency_stop=False,
        paused=False,
        uncertain=False,
    )
    base.update(overrides)
    return RiskSnapshot(**base)


def _state(**overrides) -> CycleState:
    now = datetime.now(timezone.utc)
    base = dict(
        mode="demo_filtered",
        probabilities=[0.1] * 10,
        latest_tick_epoch=1000,
        tick_received_at=now,
        features_ready=True,
        model_ready=True,
        risk=_risk(),
    )
    base.update(overrides)
    return CycleState(**base)


def test_trailing_zero_digit_is_zero():
    extracted = extract_last_digit("123.40", 2)
    assert extracted.digit_char == "0"
    assert extracted.digit_value == 0
    assert extracted.quote_text == "123.40"
    padded = extract_last_digit(123.4, 2)
    assert padded.quote_text == "123.40"
    assert padded.digit_value == 0


def test_feature_row_ignores_future_digits():
    rng = np.random.default_rng(1)
    digits = rng.integers(0, 10, 1300)
    prices = 100 + np.cumsum(rng.normal(0, 0.01, 1300))
    usable = np.ones(1300, dtype=bool)
    original = build_examples(digits, prices, usable)
    changed = digits.copy()
    changed[-5:] = (changed[-5:] + 3) % 10
    other = build_examples(changed, prices, usable)
    mask = original["index"] < 1295
    assert np.allclose(original["X"][mask], other["X"][mask])


def test_target_is_digit_five_ticks_ahead():
    rng = np.random.default_rng(2)
    digits = rng.integers(0, 10, 1200)
    prices = np.linspace(100, 110, 1200)
    usable = np.ones(1200, dtype=bool)
    examples = build_examples(digits, prices, usable)
    for index, target in zip(examples["index"], examples["y"]):
        assert target == digits[index + 5]
    assert examples["target_note"].startswith("research_proxy")


def test_split_purges_labels_that_cross_the_boundary():
    indexes = np.arange(0, 400)
    split = chronological_split(indexes, horizon=5)
    c1 = int(400 * 0.50)
    c2 = int(400 * 0.65)
    c3 = int(400 * 0.80)
    assert np.all(split["train"] + 5 < indexes[c1])
    assert np.all(split["calibration"] + 5 < indexes[c2])
    assert np.all(split["selection"] + 5 < indexes[c3])
    assert len(split["purged"]) > 0


def test_gap_removes_windows_that_cross_it():
    digits = np.arange(1200) % 10
    prices = np.ones(1200)
    usable = np.ones(1200, dtype=bool)
    usable[1100] = False
    examples = build_examples(digits, prices, usable)
    for index in examples["index"]:
        assert not (index - 999 <= 1100 <= index + 5)


def test_probabilities_stay_on_the_simplex_after_calibration():
    rng = np.random.default_rng(3)
    raw = rng.random((40, 10))
    raw = raw / raw.sum(axis=1, keepdims=True)
    y = rng.integers(0, 10, 40)
    temperature = fit_temperature(raw, y)
    scaled = apply_temperature(raw, temperature)
    assert np.isfinite(scaled).all()
    assert np.allclose(scaled.sum(axis=1), 1)
    assert (scaled >= 0).all()


def test_model_selection_uses_only_the_scores_it_is_given():
    assert select_best({"xgboost": 1.2, "uniform": 0.9}) == "uniform"


def test_training_does_not_claim_an_edge(tmp_path: Path):
    rng = np.random.default_rng(4)
    n = 1600
    digits = rng.integers(0, 10, n)
    prices = 100 + np.cumsum(rng.normal(0, 0.01, n))
    usable = np.ones(n, dtype=bool)
    examples = build_examples(digits, prices, usable)
    result = run_training(
        digits=digits,
        examples=examples,
        epochs=np.arange(n),
        seed=4,
        enable_mlp=False,
        enable_xgboost=True,
        xgb_estimators=8,
    )
    assert result["promoted"] is False
    assert result["has_demonstrated_edge"] is False
    assert result["final_test"]["result_kind"] == "historical_prediction_evaluation"
    assert result["final_test"]["payout_adjusted_return"] is None
    assert result["assumed_payout_simulation"] is None
    probs = np.full((8, 10), 0.1)
    assert as_simplex(probs).shape == (8, 10)


def test_optional_mlp_returns_a_simplex():
    rng = np.random.default_rng(5)
    features = rng.normal(size=(80, 6))
    target = np.arange(80) % 10
    model = fit_mlp(features, target, seed=5)
    probs = model.raw_probabilities(features[:12], target[:12], target, np.arange(12))
    assert np.allclose(probs.sum(axis=1), 1)


def test_ev_uses_total_payout_not_a_hard_coded_rate():
    ask = 1.0
    payout = 8.5
    assert break_even_probability(ask, payout) == pytest.approx(ask / payout)
    assert estimated_net_ev(0.2, ask, payout) == pytest.approx(0.2 * payout - ask)
    assert net_profit(True, ask, payout) == pytest.approx(payout - ask)
    assert net_profit(False, ask, payout) == pytest.approx(-ask)


def test_proposal_schema_is_classic_digitmatch():
    message = proposal_request(symbol="R_100", digit=7, stake=1, currency="USD")
    assert message["contract_type"] == "DIGITMATCH"
    assert message["duration"] == 5
    assert message["duration_unit"] == "t"
    assert message["symbol"] == "R_100"
    assert message["barrier"] == "7"
    assert "underlying_symbol" not in message
    assert "buy" not in message
    assert buy_request("abc", 1.0)["buy"] == "abc"


def test_real_and_unverified_accounts_are_rejected():
    with pytest.raises(AccountRejected):
        verify_demo_authorize({"loginid": "CR123", "is_virtual": 0, "currency": "USD"})
    with pytest.raises(AccountRejected):
        verify_demo_authorize({"loginid": "VRTC1", "currency": "USD"})
    with pytest.raises(AuthFamilyRejected):
        assert_classic_token("pat_secret")
    demo = verify_demo_authorize(
        {
            "loginid": "VRTC1",
            "is_virtual": 1,
            "landing_company_name": "virtual",
            "currency": "USD",
            "scopes": ["read", "trade"],
            "account_list": [{"loginid": "VRTC1", "is_virtual": 1}],
        }
    )
    assert demo["is_virtual"] == 1


def test_instrument_and_contract_are_not_substituted():
    resolved = resolve_volatility_100(
        [{"symbol": "R_100", "display_name": "Volatility 100 Index"}]
    )
    assert resolved["symbol"] == "R_100"
    renamed = resolve_volatility_100(
        [{"underlying_symbol": "R_100", "underlying_symbol_name": "Volatility 100 Index", "pip_size": 2}]
    )
    assert renamed["symbol"] == "R_100"
    assert renamed["pip"] == 2
    with pytest.raises(MarketUnavailable):
        resolve_volatility_100([{"symbol": "1HZ100V", "display_name": "Volatility 100 (1s) Index"}])
    with pytest.raises(MarketUnavailable):
        validate_digitmatch_five_ticks(
            {"available": [{"contract_type": "DIGITDIFF", "expiry_type": "tick", "min_contract_duration": "1t", "max_contract_duration": "10t"}]}
        )
    spec = validate_digitmatch_five_ticks(
        {
            "available": [
                {
                    "contract_type": "DIGITMATCH",
                    "expiry_type": "tick",
                    "min_contract_duration": "5t",
                    "max_contract_duration": "10t",
                }
            ]
        }
    )
    assert spec["duration"] == 5


def test_daily_loss_persists_across_a_new_store(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'risk.db'}", poolclass=NullPool)
    store = SqlStore(engine)
    store.create_schema()
    store.seed()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with store.Session() as session:
        session.add(
            DmContract(
                broker_contract_id="c1",
                digit=3,
                status="lost",
                buy_price=1,
                total_payout=8,
                profit=-19,
                purchase_time=now,
                sell_time=now,
                mode="demo_explore",
            )
        )
        session.commit()
    again = SqlStore(engine)
    snapshot = again.risk_snapshot(datetime.now(timezone.utc))
    assert snapshot.pnl_today == pytest.approx(-19)
    decision = assess(snapshot, datetime.now(timezone.utc), next_loss=2)
    assert decision.allowed is False
    assert decision.reason == "daily_loss_limit"


def test_two_workers_cannot_both_hold_the_lock(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'lock.db'}",
        poolclass=NullPool,
        connect_args={"timeout": 5},
    )
    store = SqlStore(engine)
    store.create_schema()
    store.seed()
    now = datetime.now(timezone.utc)
    assert store.try_lock("worker-a", now, ttl_seconds=60) is True
    assert store.try_lock("worker-b", now, ttl_seconds=60) is False
    store.release_lock("worker-a")
    assert store.try_lock("worker-b", now + timedelta(seconds=1), ttl_seconds=60) is True


@pytest.mark.asyncio
async def test_stale_tick_and_stale_proposal_skip_purchase():
    broker = FakeBroker()
    old = datetime.now(timezone.utc) - timedelta(seconds=30)
    stale = _state(tick_received_at=old)
    result = await run_cycle(broker, stale, datetime.now(timezone.utc))
    assert result["reason"] == "stale_tick"
    assert broker.buys == []
    broker.spot_epoch = 900
    fresh = _state(latest_tick_epoch=1000)
    result = await run_cycle(broker, fresh, datetime.now(timezone.utc))
    assert result["reason"] == "proposal_older_than_tick"
    assert broker.buys == []


@pytest.mark.asyncio
async def test_real_account_never_buys():
    broker = FakeBroker(demo=False)
    result = await run_cycle(broker, _state(), datetime.now(timezone.utc))
    assert result["reason"] == "account_rejected"
    assert broker.buys == []


@pytest.mark.asyncio
async def test_lost_purchase_response_blocks_another_buy():
    broker = FakeBroker(lose_response_after_accept=True)
    state = _state(mode="demo_explore", probabilities=[0.05] * 9 + [0.55])
    result = await run_cycle(broker, state, datetime.now(timezone.utc))
    assert result["reason"] == "uncertain_purchase"
    assert len(broker.buys) == 1
    state.probabilities = [0.9] + [0.011] * 9
    again = await run_cycle(broker, state, datetime.now(timezone.utc))
    assert again["reason"] == "uncertain_purchase"
    assert len(broker.buys) == 1


@pytest.mark.asyncio
async def test_disconnect_before_buy_does_not_mark_uncertain():
    broker = FakeBroker(disconnect_before_buy=True)
    state = _state(mode="demo_explore")
    result = await run_cycle(broker, state, datetime.now(timezone.utc))
    assert result["reason"] == "purchase_not_sent"
    assert state.uncertain is False
    assert broker.buys == []


def test_reconciliation_requires_one_clear_match():
    state = CycleState()
    state.intents.append(
        {"id": "i", "status": "uncertain", "digit": 4, "ask_price": 1.0, "total_payout": 8.0}
    )
    unclear = reconcile_unknown(
        state,
        [
            {"action_type": "buy", "amount": 1, "contract_id": 1},
            {"action_type": "buy", "amount": 1, "contract_id": 2},
        ],
    )
    assert unclear["status"] == "still_uncertain"
    clear = reconcile_unknown(state, [{"action_type": "buy", "amount": 1, "contract_id": 77}])
    assert clear["status"] == "reconciled"
    assert clear["contract_id"] == "77"


def test_settlement_keeps_open_contracts_open_until_the_broker_says_so():
    contract = {"status": "open"}
    apply_settlement(contract, {"status": "open", "is_sold": 0, "current_spot": "1.23"})
    assert contract["status"] == "open"
    apply_settlement(contract, {"status": "won", "is_sold": 1, "profit": {"value": 7.5, "sign": 1}, "exit_tick": "1.20"})
    assert contract["status"] == "won"
    assert contract["profit"] == pytest.approx(7.5)


@pytest.mark.asyncio
async def test_emergency_stop_blocks_new_orders_only():
    broker = FakeBroker()
    state = _state(mode="demo_explore", risk=_risk(emergency_stop=True))
    state.contracts.append({"broker_contract_id": "already", "status": "open"})
    result = await run_cycle(broker, state, datetime.now(timezone.utc))
    assert result["reason"] == "emergency_stop"
    assert broker.buys == []
    assert state.contracts[0]["status"] == "open"


def test_secrets_are_redacted():
    payload = {"authorize": "super-secret-token", "proposal": 1, "nested": {"api_token": "abc"}}
    cleaned = redact(payload)
    assert cleaned["authorize"] == "***"
    assert cleaned["nested"]["api_token"] == "***"
    summary = safe_message_summary({"authorize": "super-secret-token", "req_id": 3})
    assert "super-secret-token" not in str(summary)
    assert probe_summary("super-secret-token") == "keys-only"


def test_ui_state_prefers_uncertain_purchase():
    assert resolve_ui_state({"uncertain_block": True, "emergency_stop": True}) == "uncertain_purchase"
    assert resolve_ui_state({"credentials_configured": False}) == "credentials_missing"
    assert resolve_ui_state(
        {
            "credentials_configured": True,
            "auth_status": "unconfigured",
            "connection_status": "reconnecting",
            "demo_verified": False,
        }
    ) == "disconnected"
    assert resolve_ui_state(
        {
            "credentials_configured": True,
            "connection_status": "online",
            "demo_verified": True,
            "stale": True,
            "auth_status": "demo",
        }
    ) == "stale_data"


def test_conflicting_ticks_are_kept(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'ticks.db'}", poolclass=NullPool)
    store = SqlStore(engine)
    store.create_schema()
    first = normalize_observation(
        symbol="R_100", quote="123.40", epoch=10, pip_size=2, source="history", ingestion_id="a", broker_tick_id=None
    )
    second = normalize_observation(
        symbol="R_100", quote="123.41", epoch=10, pip_size=2, source="history", ingestion_id="a", broker_tick_id=None
    )
    assert store.add_tick(first) == "inserted"
    assert store.add_tick(second, previous_epoch=10, previous_quote="123.40", previous_precision=2) == "conflict"
    rows = store.series("R_100")
    assert len(rows) == 2
    assert all("conflict" in (row.quality_flags or "") for row in rows)


def test_saved_touch_ticks_rebuild_the_last_digit(tmp_path: Path):
    from app.models.tick import Tick

    engine = create_engine(f"sqlite:///{tmp_path / 'saved.db'}", poolclass=NullPool)
    store = SqlStore(engine)
    store.create_schema()
    with store.Session() as session:
        session.add_all(
            [
                Tick(
                    id=1,
                    symbol="R_100",
                    epoch=100,
                    tick_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    quote=123.4,
                    pip_size=0.01,
                    is_gap=0,
                ),
                Tick(
                    id=2,
                    symbol="1HZ100V",
                    epoch=101,
                    tick_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    quote=50.1,
                    pip_size=0.01,
                    is_gap=0,
                ),
            ]
        )
        session.commit()
    first = store.import_saved_ticks("R_100")
    second = store.import_saved_ticks("R_100")
    rows = store.series("R_100")
    assert first["inserted"] == 1
    assert first["available"] == 1
    assert second["inserted"] == 0
    assert len(rows) == 1
    assert rows[0].quote_text == "123.40"
    assert rows[0].digit_value == 0
    assert "numeric_quote_reconstructed" in (rows[0].quality_flags or "")


def test_digitmatch_reuses_the_touch_bot_login(monkeypatch):
    from app.config import settings
    from app.digitmatch.credentials import resolve_digitmatch_credentials

    monkeypatch.setattr(settings, "dm_deriv_api_token", None)
    monkeypatch.setattr(settings, "dm_deriv_app_id", None)
    monkeypatch.setattr(
        "app.digitmatch.credentials.get_trade_token",
        lambda: "same-token-as-touch-bot",
    )
    monkeypatch.setattr(
        "app.digitmatch.credentials.resolve_deriv_app_id",
        lambda: "998877",
    )
    creds = resolve_digitmatch_credentials()
    assert creds["source"] == "touch_bot"
    assert creds["token"] == "same-token-as-touch-bot"
    assert creds["app_id"] == "998877"
    assert creds["configured"] is True


def test_migration_revision_is_importable():
    import os

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    root = os.path.dirname(os.path.dirname(__file__))
    cfg = Config(os.path.join(root, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(root, "alembic"))
    scripts = ScriptDirectory.from_config(cfg)
    revisions = {revision.revision: revision.down_revision for revision in scripts.walk_revisions()}
    assert revisions["002_digitmatch"] == "001_hardening"
