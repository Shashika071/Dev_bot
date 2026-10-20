"""Fresh, demo-only Rise/Fall orders, isolated from the One-Touch execution path.

The caller must durably reserve the signal/attempt before calling ``execute_demo``
and must never retry an ``unknown`` purchase. The synthetic service owns its opt-in,
daily signal cap, scheduling, and attempt persistence. This boundary always owns
the shared server/loss gates, demo authentication, and final quote validation.
"""

from __future__ import annotations

import math
import re
import time
from typing import Any
from urllib.parse import urlsplit

from app.deriv.trade_client import DerivTradeClient
from app.execution.gates import server_execution_allowed
from app.execution.risk import load_loss_today
from app.trade_prefs import get_trade_token, load_trade_prefs, resolve_deriv_app_id


MAX_QUOTE_AGE_SECONDS = 30
MAX_FUTURE_SKEW_SECONDS = 5
DURATION_SECONDS = 300


class _Blocked(ValueError):
    """An expected refusal with a safe, machine-readable public reason."""


def _number(value: Any, reason: str) -> float:
    if isinstance(value, bool):
        raise _Blocked(reason)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise _Blocked(reason) from None
    if not math.isfinite(number):
        raise _Blocked(reason)
    return number


def _is_verified_demo(account: Any) -> bool:
    """Require positive server metadata; prefixes and truthy strings prove nothing."""
    if not isinstance(account, dict):
        return False
    account_type = str(account.get("account_type") or "").strip().lower()
    demo_type = account_type in {"demo", "virtual"}
    if account_type and not demo_type:
        return False
    flag = account.get("is_virtual")
    demo_flag = flag is True or (type(flag) is int and flag == 1)
    if flag is not None and not demo_flag:
        return False
    return demo_type or demo_flag


def _account_id(account: dict[str, Any]) -> str:
    return str(account.get("account_id") or account.get("loginid") or account.get("id") or "").strip()


def _shared_gate(stake: float, can_execute: Any = None) -> None:
    prefs = load_trade_prefs()
    if not server_execution_allowed(prefs):
        raise _Blocked("server_execution_disabled")
    limit = _number(prefs.get("daily_loss_limit", 0), "invalid_daily_loss_limit")
    if limit < 0:
        raise _Blocked("invalid_daily_loss_limit")
    if limit > 0:
        loss = _number(load_loss_today(), "daily_loss_unavailable")
        if loss < 0 or loss + stake > limit:
            raise _Blocked("daily_loss_limit_reached")
    if can_execute is not None:
        if not callable(can_execute):
            raise _Blocked("synthetic_execution_disabled")
        try:
            allowed = can_execute()
        except Exception:
            raise _Blocked("synthetic_execution_disabled") from None
        if allowed is not True:
            raise _Blocked("synthetic_execution_disabled")


def _validate_pat_socket(client: DerivTradeClient) -> None:
    parsed = urlsplit(str(client._connected_url or ""))
    if (
        parsed.scheme != "wss"
        or parsed.hostname != "api.derivws.com"
        or parsed.path.rstrip("/") != "/trading/v1/options/ws/demo"
    ):
        raise _Blocked("demo_socket_not_verified")


async def _authenticate_demo(client: DerivTradeClient) -> tuple[str, str, float]:
    if client._use_pat:
        accounts = await client.list_accounts()
        if not isinstance(accounts, list):
            raise _Blocked("demo_account_not_verified")
        # Deliberately ignore the Touch account preference, including an exact
        # real account ID. Only positively verified demo accounts are eligible.
        chosen = next((a for a in accounts if _is_verified_demo(a) and _account_id(a)), None)
        if chosen is None:
            raise _Blocked("demo_account_not_verified")
        loginid = _account_id(chosen)
        if any(
            isinstance(a, dict) and _account_id(a) == loginid and not _is_verified_demo(a)
            for a in accounts
        ):
            raise _Blocked("conflicting_account_metadata")
        if chosen.get("status") and str(chosen["status"]).lower() != "active":
            raise _Blocked("demo_account_not_active")
        await client.connect_pat(loginid)
        _validate_pat_socket(client)
        account = chosen
    else:
        account = await client.authorize()
        if not _is_verified_demo(account):
            raise _Blocked("demo_account_not_verified")
        loginid = _account_id(account)
        if not loginid:
            raise _Blocked("account_identity_missing")

    balance = await client.get_balance()
    if not isinstance(balance, dict):
        raise _Blocked("account_balance_unavailable")
    balance_id = _account_id(balance)
    if balance_id and balance_id != loginid:
        raise _Blocked("account_identity_mismatch")
    if ("account_type" in balance or "is_virtual" in balance) and not _is_verified_demo(balance):
        raise _Blocked("conflicting_account_metadata")
    account_currency = str(account.get("currency") or "").strip().upper()
    currency = str(balance.get("currency") or account_currency).strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{2,20}", currency):
        raise _Blocked("account_currency_unavailable")
    if account_currency and currency != account_currency:
        raise _Blocked("account_currency_mismatch")
    funds = _number(balance.get("balance"), "account_balance_unavailable")
    return loginid, currency, funds


def _validate_proposal(proposal: Any, *, stake: float, now: float) -> tuple[str, float, float]:
    if not isinstance(proposal, dict):
        raise _Blocked("invalid_proposal")
    proposal_id = proposal.get("id")
    if not isinstance(proposal_id, str) or not proposal_id.strip():
        raise _Blocked("proposal_id_missing")
    ask = _number(proposal.get("ask_price"), "invalid_ask_price")
    payout = _number(proposal.get("payout"), "invalid_payout")
    spot_time = _number(proposal.get("spot_time"), "quote_timestamp_missing")
    if ask <= 0 or ask > stake:
        raise _Blocked("ask_exceeds_stake" if ask > stake else "invalid_ask_price")
    if payout <= ask:
        raise _Blocked("payout_not_above_stake")
    if now - spot_time > MAX_QUOTE_AGE_SECONDS or spot_time - now > MAX_FUTURE_SKEW_SECONDS:
        raise _Blocked("stale_or_future_quote")
    if proposal.get("spot") is not None and _number(proposal["spot"], "invalid_spot") <= 0:
        raise _Blocked("invalid_spot")
    return proposal_id.strip(), ask, payout


async def execute_demo(signal: dict[str, Any], *, stake: float = 1.0) -> dict[str, Any]:
    """Buy one five-minute Volatility 100 Rise/Fall contract on a verified demo account.

    Returns ``status`` = ``bought``, ``blocked``, ``failed``, or ``unknown``.
    No credentials, OTP URLs, or raw broker exception messages enter the result.
    An exception after the buy call begins is conservatively ``unknown``; the
    caller must reconcile it with the broker and must not automatically retry.
    """
    client: DerivTradeClient | None = None
    attempted_buy = False
    context: dict[str, Any] = {"account_type": "demo"}
    try:
        stake = _number(stake, "invalid_stake")
        if not 0.35 <= stake <= 10:
            raise _Blocked("invalid_stake")
        if not isinstance(signal, dict):
            raise _Blocked("invalid_signal")
        symbol = str(signal.get("symbol") or "")
        if symbol != "R_100":
            raise _Blocked("volatility_100_symbol_required")
        direction = str(signal.get("direction") or "").upper()
        if direction not in {"RISE", "FALL"}:
            raise _Blocked("rise_fall_direction_required")
        # A nonzero barrier changes the instrument into Higher/Lower. Reject
        # every barrier field instead of forwarding or interpreting one.
        if any(k in signal for k in ("barrier", "barrier2", "barrier_input")):
            raise _Blocked("rise_fall_has_no_barrier")
        seconds = _number(signal.get("duration_seconds", DURATION_SECONDS), "invalid_duration")
        if seconds != DURATION_SECONDS:
            raise _Blocked("five_minute_duration_required")
        duration = 5
        contract_type = "CALL" if direction == "RISE" else "PUT"
        context.update(symbol=symbol, direction=direction, contract_type=contract_type,
                       duration_seconds=int(seconds), stake=stake)
        can_execute = signal.get("_can_execute")
        if not callable(can_execute):
            raise _Blocked("strategy_guard_required")
        probability = _number(signal.get("probability"), "model_probability_required")
        if not 0.5 < probability < 1:
            raise _Blocked("invalid_model_probability")
        _shared_gate(stake, can_execute)
        token = get_trade_token()
        if not token:
            raise _Blocked("token_missing")
        client = DerivTradeClient(token, app_id=resolve_deriv_app_id() or None)
        loginid, currency, funds = await _authenticate_demo(client)
        context.update(loginid=loginid, currency=currency, auth_mode="pat" if client._use_pat else "legacy")
        if funds < stake:
            raise _Blocked("insufficient_demo_balance")
        verified_socket = client.ws
        if verified_socket is None:
            raise _Blocked("authenticated_connection_lost")
        parameters = {
            "amount": stake,
            "basis": "stake",
            "contract_type": contract_type,
            "currency": currency,
            "duration": duration,
            "duration_unit": "m",
            "underlying_symbol" if client._use_pat else "symbol": symbol,
        }
        response = await client.send({"proposal": 1, **parameters})
        proposal = response.get("proposal")
        proposal_id, ask, payout = _validate_proposal(proposal, stake=stake, now=time.time())
        # Compare the selected side's model estimate with this actual ticket.
        # Accuracy alone does not cover the options premium/payout asymmetry.
        if probability < ask / payout + 0.03:
            raise _Blocked("model_edge_below_quote_breakeven")
        # A successful quote is the broker's acceptance of this exact duration.
        # If echoed contract fields are supplied, do not accept a different ticket.
        echo = response.get("echo_req")
        if isinstance(echo, dict) and any(k in echo and echo[k] != v for k, v in parameters.items()):
            raise _Blocked("proposal_ticket_mismatch")
        context.update(proposal_id=proposal_id, ask_price=ask, payout=payout,
                       break_even_probability=ask / payout,
                       quote_spot=proposal.get("spot"), quote_spot_time=float(proposal["spot_time"]),
                       estimated_expiry_epoch=int(time.time() + seconds))
        _shared_gate(stake, can_execute)
        _validate_proposal(proposal, stake=stake, now=time.time())
        if client.ws is not verified_socket:
            raise _Blocked("authenticated_connection_lost")
        if client._use_pat:
            _validate_pat_socket(client)
        # No await occurs between final socket/gate verification and buy. In
        # particular do not reconnect onto a new account or retry a purchase.
        attempted_buy = True
        try:
            bought = await client.buy(proposal_id, ask, parameters=parameters if client._use_pat else None)
        finally:
            # The quote/reservation predates the actual purchase. Count the
            # entire duration from the acknowledgement (or failed wait) so a
            # delayed broker reply cannot let the next trade overlap this one.
            context["estimated_expiry_epoch"] = math.ceil(time.time() + seconds)
        for server_record in (proposal, bought):
            if not isinstance(server_record, dict):
                continue
            try:
                expiry = _number(server_record.get("date_expiry"), "invalid_server_expiry")
            except _Blocked:
                continue
            if 0 < expiry <= 9999999999:
                context["estimated_expiry_epoch"] = max(
                    context["estimated_expiry_epoch"], math.ceil(expiry)
                )
        if not isinstance(bought, dict) or not bought.get("contract_id"):
            return {"ok": False, "status": "unknown", "reason": "purchase_unconfirmed",
                    "error": "Purchase was not confirmed; check the demo account before resuming.", **context}
        context["contract_id"] = bought["contract_id"]
        buy_price = _number(bought.get("buy_price"), "purchase_price_unconfirmed")
        if buy_price <= 0 or buy_price > stake:
            return {"ok": False, "status": "unknown", "reason": "purchase_price_unconfirmed",
                    **context}
        entry_spot = bought.get("start_spot")
        if entry_spot is not None:
            entry_spot = _number(entry_spot, "entry_spot_unconfirmed")
        return {"ok": True, "status": "bought", "buy_price": buy_price,
                "entry_spot": entry_spot, **context}
    except _Blocked as exc:
        return {"ok": False, "status": "unknown" if attempted_buy else "blocked",
                "reason": str(exc), **context}
    except Exception:
        # Broker/network exception text can include tokens or OTP URLs; keep it
        # out of persisted results and logs. Never repeat an ambiguous buy.
        return {"ok": False, "status": "unknown" if attempted_buy else "failed",
                "reason": "purchase_unconfirmed" if attempted_buy else "broker_request_failed",
                "error": ("Purchase was not confirmed; check the demo account before resuming."
                          if attempted_buy else "Broker request failed before purchase."), **context}
    finally:
        if client is not None:
            try:
                await client.close()
            except Exception:
                pass
