"""One decision cycle. At most one demo purchase, and never a blind retry."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Callable

from app.digitmatch import FRESHNESS_POLICY
from app.digitmatch.account import AccountRejected, verify_demo_authorize
from app.digitmatch.ev import break_even_probability, estimated_net_ev, stake_to_cover
from app.digitmatch.errors import PurchaseNotSent, PurchaseOutcomeUnknown, PurchaseRejected
from app.digitmatch.freshness import proposal_is_fresh, tick_is_fresh
from app.digitmatch.risk import RiskSnapshot, assess


@dataclass
class CycleState:
    mode: str = "observe"
    probabilities: list[float] | None = None
    latest_tick_epoch: int | None = None
    tick_received_at: datetime | None = None
    features_ready: bool = False
    model_ready: bool = False
    model_expired: bool = False
    owner: str = "test-owner"
    feature_epoch: int | None = None
    symbol: str = "R_100"
    duration_ticks: int = 5
    execution_eligible: bool = False
    proposal_spacing_seconds: float = 0
    max_tick_age_seconds: float = 5
    max_proposal_age_seconds: float = 3
    margin: float = 0.02
    live_epoch: Callable[[], int | None] | None = None
    live_controls: Callable[[], dict] | None = None
    fresh_risk: Callable | None = None
    commit_intent: Callable[[dict], None] | None = None
    decisions: list = field(default_factory=list)
    intents: list = field(default_factory=list)
    contracts: list = field(default_factory=list)
    uncertain: bool = False
    uncertain_reason: str | None = None
    lock_owner: str | None = None
    risk: RiskSnapshot | None = None
    audits: list = field(default_factory=list)
    recent_digits: list[int] = field(default_factory=list)
    last_chosen_digit: int | None = None

    def try_lock(self, owner: str) -> bool:
        if self.lock_owner not in (None, owner):
            return False
        self.lock_owner = owner
        return True

    def release_lock(self, owner: str) -> None:
        if self.lock_owner == owner:
            self.lock_owner = None


def leading_digit(probabilities: list[float]) -> int | None:
    """Return the one digit that is ahead. A tie is not a prediction."""
    if not probabilities:
        return None
    best = max(probabilities)
    leaders = [index for index, value in enumerate(probabilities) if abs(float(value) - float(best)) <= 1e-9]
    if len(leaders) != 1:
        return None
    return leaders[0]


def most_shown_digit(digits: list[int] | None, avoid: int | None = None) -> int | None:
    """Most common digit in the last 100. The previous pick is skipped when another digit exists."""
    if not digits:
        return None
    window: list[int] = []
    for value in digits[-100:]:
        try:
            digit = int(value)
        except (TypeError, ValueError):
            continue
        if 0 <= digit <= 9:
            window.append(digit)
    if len(window) < 100:
        return None
    counts = [0] * 10
    for digit in window:
        counts[digit] += 1
    order = sorted(range(10), key=lambda digit: (-counts[digit], digit))
    for digit in order:
        if counts[digit] == 0:
            continue
        if avoid is not None and digit == int(avoid):
            continue
        return digit
    top = order[0]
    return top if counts[top] else None


def chosen_digit(state: CycleState) -> int | None:
    if not state.probabilities:
        return None
    leader = leading_digit(list(state.probabilities))
    if leader is not None:
        return leader
    return most_shown_digit(state.recent_digits, state.last_chosen_digit)


def _scored(state: CycleState) -> dict:
    if not state.probabilities:
        return {}
    payload = {"probabilities": state.probabilities}
    digit = chosen_digit(state)
    if digit is not None:
        payload["digit"] = digit
    return payload


def _record(state: CycleState, action: str, reason: str, **extra) -> dict:
    row = {"action": action, "reason": reason, "mode": state.mode, "policy": FRESHNESS_POLICY, **extra}
    state.decisions.append(row)
    return row


async def run_cycle(broker, state: CycleState, now: datetime) -> dict:
    risk = state.risk
    if risk is None:
        return _record(state, "skip", "risk_unavailable")
    if state.uncertain or risk.uncertain:
        return _record(state, "skip", "uncertain_purchase")

    try:
        account = verify_demo_authorize(await broker.authorize_payload())
    except AccountRejected as exc:
        return _record(state, "skip", "account_rejected", detail=str(exc))

    fresh = tick_is_fresh(
        received_at=state.tick_received_at,
        now=now,
        max_age_seconds=state.max_tick_age_seconds,
    )
    scored = _scored(state)
    if not fresh.ok:
        return _record(state, "skip", fresh.reason, **scored)

    if state.mode == "observe":
        return _record(state, "skip", "observe_mode", **scored)

    if not state.model_ready:
        return _record(state, "skip", "no_model")
    if not state.features_ready or state.probabilities is None:
        return _record(state, "skip", "window_not_clean")
    if state.model_expired:
        return _record(state, "skip", "model_expired", **scored)

    gate = assess(risk, now, next_loss=risk.stake)
    if not gate.allowed:
        return _record(state, "skip", gate.reason, **scored)

    forced = None
    if state.probabilities is not None and leading_digit(list(state.probabilities)) is None:
        forced = most_shown_digit(state.recent_digits, state.last_chosen_digit)
        if forced is None:
            return _record(state, "skip", "flat_prediction", probabilities=state.probabilities)

    if state.feature_epoch is not None and state.latest_tick_epoch != state.feature_epoch:
        return _record(state, "skip", "prediction_overtaken", **scored)

    epoch_before = state.latest_tick_epoch
    quotes = []
    quote_digits = [forced] if forced is not None else list(range(10))
    for digit in quote_digits:
        if digit and state.proposal_spacing_seconds > 0:
            await asyncio.sleep(state.proposal_spacing_seconds)
        started = time.monotonic()
        proposal = await broker.proposal(digit, risk.stake, account["currency"] or "USD")
        proposal_age = time.monotonic() - started
        _refresh_live(state)
        quote_fresh = proposal_is_fresh(
            proposal_epoch=proposal.get("spot_time"),
            latest_tick_epoch=state.latest_tick_epoch,
            proposal_age_seconds=proposal_age,
            max_proposal_age_seconds=state.max_proposal_age_seconds,
        )
        if not quote_fresh.ok:
            return _record(state, "skip", quote_fresh.reason, **scored)
        try:
            ask = float(proposal["ask_price"])
            payout = float(proposal["total_payout"])
        except (TypeError, ValueError):
            return _record(state, "skip", "proposal_unreadable", **scored)
        if ask <= 0 or payout <= ask:
            return _record(state, "skip", "proposal_unreadable", **scored)
        probability = float(state.probabilities[digit])
        quotes.append(
            {
                "digit": digit,
                "proposal_id": proposal["id"],
                "ask_price": ask,
                "total_payout": payout,
                "break_even": break_even_probability(ask, payout),
                "expected_value": estimated_net_ev(probability, ask, payout),
                "probability": probability,
                "age_seconds": proposal_age,
            }
        )

    _refresh_live(state)
    if state.feature_epoch is not None and state.latest_tick_epoch != state.feature_epoch:
        return _record(state, "skip", "prediction_overtaken", **scored)
    if state.latest_tick_epoch != epoch_before:
        return _record(state, "skip", "tick_arrived_before_submit", **scored)

    best = max(quotes, key=lambda row: (row["expected_value"], -row["digit"]))
    if state.mode == "demo_filtered":
        if not state.execution_eligible:
            return _record(
                state,
                "skip",
                "filtered_evidence_missing",
                digit=best["digit"],
                break_even=best["break_even"],
                expected_value=best["expected_value"],
                probabilities=state.probabilities,
            )
        if best["probability"] < best["break_even"] + state.margin or best["expected_value"] <= 0:
            return _record(
                state,
                "skip",
                "no_qualifying_signal",
                digit=best["digit"],
                break_even=best["break_even"],
                expected_value=best["expected_value"],
                probabilities=state.probabilities,
            )
    elif state.mode != "demo_explore":
        return _record(state, "skip", "unknown_mode")

    order_stake = float(state.risk.stake)
    if state.risk.recovery_loss > 0:
        room = state.risk.daily_loss_limit + state.risk.pnl_today
        needed = stake_to_cover(
            loss=state.risk.recovery_loss,
            target_profit=state.risk.win_profit_target,
            ask_price=best["ask_price"],
            total_payout=best["total_payout"],
            floor=order_stake,
            ceiling=min(20.0, room),
        )
        if needed is None:
            return _record(state, "skip", "recovery_stake_capped", digit=best["digit"])
        order_stake = needed
        if abs(order_stake - best["ask_price"]) > 0.009:
            resized = await broker.proposal(best["digit"], order_stake, account["currency"] or "USD")
            try:
                best["ask_price"] = float(resized["ask_price"])
                best["total_payout"] = float(resized["total_payout"])
                best["proposal_id"] = resized["id"]
                best["break_even"] = break_even_probability(best["ask_price"], best["total_payout"])
                best["expected_value"] = estimated_net_ev(best["probability"], best["ask_price"], best["total_payout"])
            except (TypeError, ValueError, KeyError):
                return _record(state, "skip", "proposal_changed", digit=best["digit"])

    confirmed = await broker.proposal(best["digit"], order_stake, account["currency"] or "USD")
    try:
        confirmed_ask = float(confirmed["ask_price"])
        confirmed_payout = float(confirmed["total_payout"])
    except (TypeError, ValueError, KeyError):
        return _record(state, "skip", "proposal_changed", digit=best["digit"])
    if (
        confirmed.get("id") != best["proposal_id"]
        or abs(confirmed_ask - best["ask_price"]) > 1e-9
        or abs(confirmed_payout - best["total_payout"]) > 1e-9
    ):
        return _record(state, "skip", "proposal_changed", digit=best["digit"])

    _refresh_live(state)
    if state.feature_epoch is not None and state.latest_tick_epoch != state.feature_epoch:
        return _record(state, "skip", "prediction_overtaken", digit=best["digit"])
    if state.latest_tick_epoch != epoch_before:
        return _record(state, "skip", "tick_arrived_before_submit", digit=best["digit"])
    if state.mode not in {"demo_explore", "demo_filtered"}:
        return _record(state, "skip", "mode_changed", digit=best["digit"])
    if state.mode == "demo_filtered" and not state.execution_eligible:
        return _record(state, "skip", "filtered_evidence_missing", digit=best["digit"])

    risk = state.risk
    again = assess(risk, now, next_loss=best["ask_price"])
    if not again.allowed:
        return _record(state, "skip", again.reason, digit=best["digit"])

    if not state.try_lock(state.owner):
        return _record(state, "skip", "execution_lock_held")

    intent = {
        "id": str(uuid.uuid4()),
        "status": "reserved",
        "digit": best["digit"],
        "proposal_id": best["proposal_id"],
        "ask_price": best["ask_price"],
        "total_payout": best["total_payout"],
        "created_at": now,
        "symbol": state.symbol,
        "duration_ticks": state.duration_ticks,
        "feature_epoch": state.feature_epoch if state.feature_epoch is not None else state.latest_tick_epoch,
        "mode": state.mode,
    }
    state.intents.append(intent)
    try:
        if not _store_intent(state, intent):
            intent["status"] = "not_sent"
            return _record(state, "skip", "intent_not_durable", digit=best["digit"])
        intent["status"] = "submitting"
        if not _store_intent(state, intent):
            intent["status"] = "not_sent"
            _store_intent(state, intent)
            return _record(state, "skip", "intent_not_durable", digit=best["digit"])
        try:
            bought = await broker.buy(best["proposal_id"], best["ask_price"])
        except PurchaseNotSent as exc:
            intent["status"] = "not_sent"
            intent["error"] = str(exc)
            _store_intent(state, intent)
            return _record(state, "skip", "purchase_not_sent", digit=best["digit"])
        except PurchaseOutcomeUnknown as exc:
            intent["status"] = "uncertain"
            intent["error"] = str(exc)
            state.uncertain = True
            state.uncertain_reason = str(exc)
            state.audits.append({"event": "purchase_outcome_unknown", "detail": str(exc)})
            _store_intent(state, intent)
            return _record(state, "blocked", "uncertain_purchase", digit=best["digit"])
        except PurchaseRejected as exc:
            intent["status"] = "rejected"
            intent["error"] = str(exc)
            _store_intent(state, intent)
            return _record(state, "skip", "purchase_rejected", digit=best["digit"])
        intent["status"] = "accepted"
        intent["contract_id"] = bought["contract_id"]
        _store_intent(state, intent)
        state.contracts.append(
            {
                "broker_contract_id": bought["contract_id"],
                "digit": best["digit"],
                "status": "open",
                "buy_price": bought.get("buy_price"),
                "total_payout": bought.get("total_payout"),
                "mode": state.mode,
            }
        )
        return _record(
            state,
            "buy",
            "submitted",
            digit=best["digit"],
            contract_id=bought["contract_id"],
            break_even=best["break_even"],
            expected_value=best["expected_value"],
            ask_price=best["ask_price"],
            total_payout=best["total_payout"],
            probabilities=state.probabilities,
            proposal_age_seconds=best["age_seconds"],
        )
    finally:
        state.release_lock(state.owner)


def _refresh_live(state: CycleState) -> None:
    if callable(state.live_epoch):
        value = state.live_epoch()
        if value is not None:
            state.latest_tick_epoch = int(value)
    if state.risk is not None and callable(state.live_controls):
        update = state.live_controls() or {}
        changes = {}
        if "emergency_stop" in update:
            changes["emergency_stop"] = bool(update["emergency_stop"])
        if "paused" in update:
            changes["paused"] = bool(update["paused"])
        if "uncertain" in update:
            changes["uncertain"] = bool(update["uncertain"])
        if changes:
            state.risk = replace(state.risk, **changes)
        if update.get("mode"):
            state.mode = str(update["mode"])
    if callable(state.fresh_risk):
        snapshot = state.fresh_risk()
        if snapshot is not None:
            state.risk = snapshot


def _store_intent(state: CycleState, intent: dict) -> bool:
    if not callable(state.commit_intent):
        return True
    try:
        state.commit_intent(dict(intent))
    except Exception:
        return False
    return True


def _statement_matches(intent: dict, row: dict) -> bool:
    if str(row.get("action_type") or "").lower() not in {"buy", "buy_contract"}:
        return False
    if row.get("contract_id") is None:
        return False
    amount = row.get("amount")
    if amount is None:
        return False
    if abs(abs(float(amount)) - float(intent["ask_price"])) > 0.01:
        return False
    symbol = row.get("symbol") or row.get("underlying")
    expected_symbol = intent.get("symbol") or "R_100"
    if symbol and str(symbol) != expected_symbol:
        return False
    if row.get("digit") is not None and int(row["digit"]) != int(intent["digit"]):
        return False
    duration = row.get("duration") if row.get("duration") is not None else row.get("duration_ticks")
    expected_duration = int(intent.get("duration_ticks") or 5)
    if duration is not None and int(duration) != expected_duration:
        return False
    identity_hits = 0
    if symbol:
        identity_hits += 1
    if row.get("digit") is not None:
        identity_hits += 1
    if duration is not None:
        identity_hits += 1
    shortcode = str(row.get("shortcode") or "")
    if shortcode:
        tokens = [part for part in shortcode.replace("::", "_").split("_") if part]
        if expected_symbol not in shortcode:
            return False
        if str(intent["digit"]) not in tokens:
            return False
        if not any(part in {"5", "5T", "5t"} for part in tokens):
            return False
        identity_hits += 2
    if identity_hits < 2:
        return False
    transaction_time = row.get("transaction_time")
    created_epoch = intent.get("feature_epoch")
    if transaction_time is not None and created_epoch is not None:
        if abs(int(transaction_time) - int(created_epoch)) > 180:
            return False
    return True


def reconcile_unknown(state: CycleState, statement: list[dict]) -> dict:
    """Match a lost buy to broker rows. Amount alone is not an identity."""
    pending = [row for row in state.intents if row["status"] == "uncertain"]
    if not pending:
        return {"status": "clear"}
    intent = pending[-1]
    matches = [row for row in statement if _statement_matches(intent, row)]
    if len(matches) != 1:
        state.uncertain = True
        return {"status": "still_uncertain", "candidates": len(matches)}
    intent["status"] = "reconciled"
    intent["contract_id"] = str(matches[0]["contract_id"])
    state.uncertain = False
    state.uncertain_reason = None
    state.contracts.append(
        {
            "broker_contract_id": str(matches[0]["contract_id"]),
            "digit": intent["digit"],
            "status": "open",
            "buy_price": intent["ask_price"],
            "total_payout": intent["total_payout"],
            "mode": state.mode,
        }
    )
    return {"status": "reconciled", "contract_id": intent["contract_id"]}


def _number(value) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, dict):
        raw = value.get("value", value.get("display"))
        if raw in (None, ""):
            return None
        number = abs(float(raw))
        sign = value.get("sign")
        if sign is not None:
            try:
                if int(sign) < 0:
                    return -number
                if int(sign) > 0:
                    return number
            except (TypeError, ValueError):
                pass
        if value.get("is_win") is False:
            return -number
        return number
    return float(value)


def apply_settlement(contract: dict, update: dict) -> dict:
    status = str(update.get("status") or "").lower()
    sold = update.get("is_sold") in (1, True, "1")
    expired = update.get("is_expired") in (1, True, "1")
    if status in {"won", "lost", "sold"} or sold or expired:
        contract["status"] = status or ("closed" if sold or expired else contract["status"])
        profit = _number(update.get("profit"))
        if profit is not None:
            contract["profit"] = profit
        contract["exit_spot"] = update.get("exit_tick")
        contract["entry_spot"] = update.get("entry_tick") or contract.get("entry_spot")
    else:
        contract["status"] = "open"
        contract["current_spot"] = update.get("current_spot")
    return contract
