"""Execution boundary tests use only fake sockets, accounts, and credentials."""

import asyncio

import pytest

from app.synthetic import execution


NOW = 1_790_000_000


class FakeClient:
    def __init__(self):
        self._use_pat = False
        self._connected_url = None
        self.ws = None
        self.auth = {"loginid": "VRTC123", "is_virtual": 1, "currency": "EUR"}
        self.accounts = []
        self.balance = {"balance": 100, "currency": "EUR"}
        self.proposal = {"id": "quote-1", "ask_price": "1.0", "payout": "1.85",
                         "spot_time": NOW, "spot": 1.085}
        self.echo = None
        self.bought = {"contract_id": 42, "buy_price": 1, "start_spot": 1.0851}
        self.buy_error = None
        self.requests = []
        self.buys = []
        self.connected_accounts = []
        self.closed = False
        self.after_proposal = None
        self.after_buy = None
        self.pat_url = "wss://api.derivws.com/trading/v1/options/ws/demo"

    async def authorize(self):
        self.ws = object()
        return self.auth

    async def list_accounts(self):
        return self.accounts

    async def connect_pat(self, account_id):
        self.connected_accounts.append(account_id)
        self.ws = object()
        self._connected_url = self.pat_url

    async def get_balance(self):
        return self.balance

    async def send(self, payload):
        self.requests.append(payload)
        if self.after_proposal:
            self.after_proposal()
        return {"proposal": self.proposal, "echo_req": self.echo}

    async def buy(self, proposal_id, price, *, parameters=None):
        self.buys.append({"proposal_id": proposal_id, "price": price, "parameters": parameters})
        if self.after_buy:
            self.after_buy()
        if self.buy_error:
            raise self.buy_error
        return self.bought

    async def close(self):
        self.closed = True


@pytest.fixture
def fake(monkeypatch):
    client = FakeClient()
    prefs = {"server_execution_allowed": True, "daily_loss_limit": 0,
             "trade_account_prefer": "real", "trade_account_id": "CR_REAL"}
    monkeypatch.setattr(execution, "DerivTradeClient", lambda token, app_id=None: client)
    monkeypatch.setattr(execution, "load_trade_prefs", lambda: prefs)
    monkeypatch.setattr(execution, "get_trade_token", lambda: "fake-test-credential")
    monkeypatch.setattr(execution, "resolve_deriv_app_id", lambda: "test-app")
    monkeypatch.setattr(execution, "load_loss_today", lambda: 0)
    monkeypatch.setattr(execution.time, "time", lambda: NOW)
    return client, prefs


def _execute(signal, **kwargs):
    """Drain an all-fake async call; any real IO/event-loop yield fails the test.

    None of the fake methods suspend. This also keeps safety boundary tests
    independent of the host's asyncio socket/proactor implementation.
    """
    coroutine = execution.execute_demo(signal, **kwargs)
    try:
        coroutine.send(None)
    except StopIteration as finished:
        return finished.value
    finally:
        coroutine.close()
    raise AssertionError("Execution unexpectedly suspended with an entirely fake broker")


def ticket(**extra):
    return {"symbol": "R_100", "direction": "RISE", "probability": 0.65, "_can_execute": lambda: True, **extra}


@pytest.mark.parametrize("probability", [None, float("nan"), 0.5, 1, 0.55])
def test_missing_invalid_or_uneconomic_model_estimate_blocks_buy(fake, probability):
    client, _ = fake
    result = _execute(ticket(probability=probability))
    assert result["status"] == "blocked"
    assert client.buys == []


def test_strategy_guard_is_mandatory(fake):
    client, _ = fake
    result = _execute(ticket(_can_execute=None))
    assert result["reason"] == "strategy_guard_required"
    assert client.buys == client.requests == []


@pytest.mark.parametrize("direction,contract_type", [("RISE", "CALL"), ("FALL", "PUT")])
def test_legacy_rise_fall_uses_account_currency_without_barrier(fake, direction, contract_type):
    client, _ = fake
    result = _execute(ticket(direction=direction))
    assert result["ok"] is True
    assert result["status"] == "bought"
    assert result["contract_id"] == 42
    assert result["entry_spot"] == 1.0851
    assert result["quote_spot"] == 1.085
    assert result["break_even_probability"] == pytest.approx(1 / 1.85)
    assert client.requests == [{"proposal": 1, "amount": 1, "basis": "stake",
                                "contract_type": contract_type, "currency": "EUR",
                                "duration": 5, "duration_unit": "m", "symbol": "R_100"}]
    assert client.buys == [{"proposal_id": "quote-1", "price": 1.0, "parameters": None}]
    assert client.closed is True


@pytest.mark.parametrize("seconds", [3600, 7200, 10800, 18000, 86400])
def test_only_chosen_five_minute_duration_is_allowed(fake, seconds):
    client, _ = fake
    result = _execute(ticket(duration_seconds=seconds))
    assert result["reason"] == "five_minute_duration_required"
    assert client.ws is None
    assert client.requests == client.buys == []


def test_delayed_acknowledgement_extends_conservative_expiry(fake, monkeypatch):
    client, _ = fake
    now = [NOW]
    monkeypatch.setattr(execution.time, "time", lambda: now[0])
    client.after_buy = lambda: now.__setitem__(0, NOW + 90.25)
    client.bought["date_expiry"] = NOW + 300
    result = _execute(ticket())
    assert result["status"] == "bought"
    assert result["estimated_expiry_epoch"] == NOW + 300 + 91


@pytest.mark.parametrize("record", ["proposal", "bought"])
def test_later_valid_server_expiry_is_preserved(fake, record):
    client, _ = fake
    getattr(client, record)["date_expiry"] = str(NOW + 600)
    result = _execute(ticket())
    assert result["status"] == "bought"
    assert result["estimated_expiry_epoch"] == NOW + 600


@pytest.mark.parametrize("expiry", [None, True, "invalid", "NaN", float("inf"), -1, 10_000_000_000])
def test_invalid_optional_server_expiry_uses_acknowledgement_floor(fake, expiry):
    client, _ = fake
    client.bought["date_expiry"] = expiry
    result = _execute(ticket())
    assert result["status"] == "bought"
    assert result["estimated_expiry_epoch"] == NOW + 300


def test_ambiguous_delayed_purchase_also_extends_expiry(fake, monkeypatch):
    client, _ = fake
    now = [NOW]
    monkeypatch.setattr(execution.time, "time", lambda: now[0])
    client.after_buy = lambda: now.__setitem__(0, NOW + 30)
    client.buy_error = asyncio.TimeoutError()
    result = _execute(ticket())
    assert result["status"] == "unknown"
    assert result["estimated_expiry_epoch"] == NOW + 300 + 30
    assert len(client.buys) == 1


@pytest.mark.parametrize("account", [
    {"loginid": "CR123", "is_virtual": 0},
    {"loginid": "VRTC123"},
    {"loginid": "VRTC123", "is_virtual": "true"},
    {"loginid": "VRTC123", "is_virtual": "1"},
    {"loginid": "VRTC123", "is_virtual": True, "account_type": "real"},
    {"loginid": "VRTC123", "is_virtual": False, "account_type": "demo"},
    {"loginid": "VRTC123", "account_type": "demonstration-real"},
])
def test_real_unknown_or_conflicting_legacy_account_never_quotes_or_buys(fake, account):
    client, _ = fake
    client.auth = account
    result = _execute(ticket())
    assert result["reason"] == "demo_account_not_verified"
    assert client.requests == client.buys == []
    assert client.closed is True


def test_pat_ignores_real_preference_and_selects_verified_demo(fake):
    client, _ = fake
    client._use_pat = True
    client.accounts = [
        {"account_id": "CR_REAL", "account_type": "real", "currency": "USD"},
        {"account_id": "DOT_UNKNOWN", "group": "demo", "currency": "USD"},
        {"account_id": "DOT_SAFE", "account_type": "demo", "currency": "EUR", "status": "active"},
    ]
    result = _execute(ticket(direction="FALL"))
    assert result["status"] == "bought"
    assert result["loginid"] == "DOT_SAFE"
    assert client.connected_accounts == ["DOT_SAFE"]
    parameters = client.buys[0]["parameters"]
    assert parameters["underlying_symbol"] == "R_100"
    assert parameters["contract_type"] == "PUT"
    assert parameters["duration"] == 5
    assert "symbol" not in parameters
    assert "barrier" not in parameters
    assert client.requests[0] == {"proposal": 1, **parameters}


@pytest.mark.parametrize("accounts", [
    [{"account_id": "DOT_REAL", "account_type": "real"}],
    [{"account_id": "DOT_UNKNOWN", "group": "demo"}],
    [{"account_id": "DOT_CONFLICT", "account_type": "demo", "is_virtual": 0}],
    [],
])
def test_pat_never_uses_prefixes_or_group_to_infer_demo(fake, accounts):
    client, _ = fake
    client._use_pat = True
    client.accounts = accounts
    result = _execute(ticket())
    assert result["reason"] == "demo_account_not_verified"
    assert client.connected_accounts == client.requests == client.buys == []


@pytest.mark.parametrize("url", [
    "wss://api.derivws.com/trading/v1/options/ws/real",
    "wss://api.derivws.com/trading/v1/options/ws/public",
    "wss://other.invalid/trading/v1/options/ws/demo",
])
def test_pat_rejects_unverified_socket_endpoint(fake, url):
    client, _ = fake
    client._use_pat = True
    client.accounts = [{"account_id": "DOT_SAFE", "account_type": "demo", "currency": "EUR"}]
    client.pat_url = url
    result = _execute(ticket())
    assert result["reason"] == "demo_socket_not_verified"
    assert client.buys == []


@pytest.mark.parametrize("field,value,reason", [
    ("ask_price", None, "invalid_ask_price"),
    ("ask_price", "NaN", "invalid_ask_price"),
    ("ask_price", float("inf"), "invalid_ask_price"),
    ("ask_price", 0, "invalid_ask_price"),
    ("ask_price", 1.01, "ask_exceeds_stake"),
    ("payout", "NaN", "invalid_payout"),
    ("payout", 1, "payout_not_above_stake"),
    ("spot_time", None, "quote_timestamp_missing"),
    ("spot_time", float("inf"), "quote_timestamp_missing"),
    ("spot_time", NOW - 31, "stale_or_future_quote"),
    ("spot_time", NOW + 6, "stale_or_future_quote"),
    ("spot", float("nan"), "invalid_spot"),
    ("id", "", "proposal_id_missing"),
])
def test_malformed_or_stale_quote_never_buys(fake, field, value, reason):
    client, _ = fake
    client.proposal[field] = value
    result = _execute(ticket())
    assert result["status"] == "blocked"
    assert result["reason"] == reason
    assert client.buys == []


@pytest.mark.parametrize("error", [asyncio.TimeoutError(), ConnectionError("secret-test-token")])
def test_ambiguous_purchase_is_unknown_and_never_retried_or_leaks_error(fake, error):
    client, _ = fake
    client.buy_error = error
    result = _execute(ticket())
    assert result["status"] == "unknown"
    assert result["reason"] == "purchase_unconfirmed"
    assert result["ok"] is False
    assert len(client.buys) == 1
    assert "secret-test-token" not in str(result)
    assert client.closed is True


def test_missing_buy_confirmation_is_unknown(fake):
    client, _ = fake
    client.bought = {}
    result = _execute(ticket())
    assert result["status"] == "unknown"
    assert len(client.buys) == 1


def test_missing_buy_price_preserves_contract_id_for_reconciliation(fake):
    client, _ = fake
    client.bought = {"contract_id": 42}
    result = _execute(ticket())
    assert result["status"] == "unknown"
    assert result["contract_id"] == 42
    assert len(client.buys) == 1


def test_pat_conflicting_records_for_same_account_never_connect(fake):
    client, _ = fake
    client._use_pat = True
    client.accounts = [{"account_id": "DOT123", "account_type": "demo"},
                       {"account_id": "DOT123", "account_type": "real"}]
    result = _execute(ticket())
    assert result["reason"] == "conflicting_account_metadata"
    assert client.connected_accounts == client.buys == []


def test_balance_cannot_switch_to_another_account(fake):
    client, _ = fake
    client.balance["loginid"] = "CR_REAL"
    result = _execute(ticket())
    assert result["reason"] == "account_identity_mismatch"
    assert client.requests == client.buys == []


def test_insufficient_demo_balance_never_buys(fake):
    client, _ = fake
    client.balance["balance"] = 0.5
    result = _execute(ticket())
    assert result["reason"] == "insufficient_demo_balance"
    assert client.requests == client.buys == []


def test_server_gate_is_checked_before_authentication(fake):
    client, prefs = fake
    prefs["server_execution_allowed"] = False
    result = _execute(ticket())
    assert result["reason"] == "server_execution_disabled"
    assert client.ws is None
    assert client.requests == client.buys == []


def test_rechecks_server_kill_switch_after_quote(fake):
    client, prefs = fake
    client.after_proposal = lambda: prefs.update(server_execution_allowed=False)
    result = _execute(ticket())
    assert result["reason"] == "server_execution_disabled"
    assert client.buys == []


def test_rechecks_synthetic_running_callback_after_quote_without_serializing_it(fake):
    client, _ = fake
    enabled = [True]
    client.after_proposal = lambda: enabled.__setitem__(0, False)
    result = _execute(ticket(_can_execute=lambda: enabled[0]))
    assert result["reason"] == "synthetic_execution_disabled"
    assert "_can_execute" not in result
    assert client.buys == []


def test_does_not_buy_after_socket_changes(fake):
    client, _ = fake
    client.after_proposal = lambda: setattr(client, "ws", object())
    result = _execute(ticket())
    assert result["reason"] == "authenticated_connection_lost"
    assert client.buys == []


def test_rejects_changed_ticket_in_echo(fake):
    client, _ = fake
    client.echo = {"duration": 1, "duration_unit": "h"}
    result = _execute(ticket())
    assert result["reason"] == "proposal_ticket_mismatch"
    assert client.buys == []


def test_respects_positive_shared_loss_limit(fake, monkeypatch):
    client, prefs = fake
    prefs["daily_loss_limit"] = 10
    monkeypatch.setattr(execution, "load_loss_today", lambda: 9.5)
    result = _execute(ticket())
    assert result["reason"] == "daily_loss_limit_reached"
    assert client.requests == client.buys == []


@pytest.mark.parametrize("extra", [
    {"symbol": "frxGBPUSD"}, {"direction": "UP"}, {"duration_seconds": 60},
    {"duration_seconds": 14401}, {"duration_seconds": float("inf")},
    {"duration_seconds": 90000}, {"barrier": "+0.01"}, {"barrier_input": None},
])
def test_invalid_signal_never_reaches_account_or_broker(fake, extra):
    client, _ = fake
    result = _execute(ticket(**extra))
    assert result["status"] == "blocked"
    assert client.ws is None
    assert client.requests == client.buys == []


@pytest.mark.parametrize("stake", [float("nan"), float("inf"), True, 0, -1, 10.01])
def test_invalid_or_excessive_demo_stake_never_buys(fake, stake):
    client, _ = fake
    result = _execute(ticket(), stake=stake)
    assert result["reason"] == "invalid_stake"
    assert client.buys == []
