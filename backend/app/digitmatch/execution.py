"""One decision cycle. At most one demo purchase, and never a blind retry."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from app.digitmatch import FRESHNESS_POLICY
from app.digitmatch.account import AccountRejected, verify_demo_authorize
from app.digitmatch.ev import break_even_probability, estimated_net_ev
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
    max_tick_age_seconds: float = 5
    max_proposal_age_seconds: float = 3
    margin: float = 0.02
    decisions: list = field(default_factory=list)
    intents: list = field(default_factory=list)
    contracts: list = field(default_factory=list)
    uncertain: bool = False
    uncertain_reason: str | None = None
    lock_owner: str | None = None
    risk: RiskSnapshot | None = None
    audits: list = field(default_factory=list)

    def try_lock(self, owner: str) -> bool:
        if self.lock_owner not in (None, owner):
            return False
        self.lock_owner = owner
        return True

    def release_lock(self, owner: str) -> None:
        if self.lock_owner == owner:
            self.lock_owner = None


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
    if not fresh.ok:
        return _record(state, "skip", fresh.reason)

    if state.mode == "observe":
        return _record(state, "skip", "observe_mode", probabilities=state.probabilities)

    if not state.features_ready or not state.model_ready or state.probabilities is None:
        return _record(state, "skip", "no_model")
    if state.model_expired:
        return _record(state, "skip", "model_expired")

    gate = assess(risk, now, next_loss=risk.stake)
    if not gate.allowed:
        return _record(state, "skip", gate.reason)

    epoch_before = state.latest_tick_epoch
    quotes = []
    for digit in range(10):
        proposal = await broker.proposal(digit, risk.stake, account["currency"] or "USD")
        quote_fresh = proposal_is_fresh(
            proposal_epoch=proposal.get("spot_time"),
            latest_tick_epoch=state.latest_tick_epoch,
            proposal_age_seconds=0,
            max_proposal_age_seconds=state.max_proposal_age_seconds,
        )
        if not quote_fresh.ok:
            return _record(state, "skip", quote_fresh.reason)
        ask = float(proposal["ask_price"])
        payout = float(proposal["total_payout"])
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
            }
        )

    if state.latest_tick_epoch != epoch_before:
        return _record(state, "skip", "tick_arrived_before_submit")

    best = max(quotes, key=lambda row: (row["expected_value"], -row["digit"]))
    if state.mode == "demo_filtered":
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

    again = assess(risk, now, next_loss=best["ask_price"])
    if not again.allowed:
        return _record(state, "skip", again.reason, digit=best["digit"])
    if risk.emergency_stop:
        return _record(state, "skip", "emergency_stop")

    if not state.try_lock(state.owner):
        return _record(state, "skip", "execution_lock_held")

    intent = {
        "id": str(uuid.uuid4()),
        "status": "submitting",
        "digit": best["digit"],
        "proposal_id": best["proposal_id"],
        "ask_price": best["ask_price"],
        "total_payout": best["total_payout"],
        "created_at": now,
    }
    state.intents.append(intent)
    try:
        try:
            bought = await broker.buy(best["proposal_id"], best["ask_price"])
        except PurchaseNotSent as exc:
            intent["status"] = "not_sent"
            intent["error"] = str(exc)
            return _record(state, "skip", "purchase_not_sent", digit=best["digit"])
        except PurchaseOutcomeUnknown as exc:
            intent["status"] = "uncertain"
            intent["error"] = str(exc)
            state.uncertain = True
            state.uncertain_reason = str(exc)
            state.audits.append({"event": "purchase_outcome_unknown", "detail": str(exc)})
            return _record(state, "blocked", "uncertain_purchase", digit=best["digit"])
        except PurchaseRejected as exc:
            intent["status"] = "rejected"
            intent["error"] = str(exc)
            return _record(state, "skip", "purchase_rejected", digit=best["digit"])
        intent["status"] = "accepted"
        intent["contract_id"] = bought["contract_id"]
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
        )
    finally:
        state.release_lock(state.owner)


def reconcile_unknown(state: CycleState, statement: list[dict]) -> dict:
    """Match a lost buy to broker rows. Ambiguous matches stay blocked."""
    pending = [row for row in state.intents if row["status"] == "uncertain"]
    if not pending:
        return {"status": "clear"}
    intent = pending[-1]
    matches = []
    for row in statement:
        if str(row.get("action_type") or "").lower() not in {"buy", "buy_contract"}:
            continue
        amount = row.get("amount")
        if amount is None:
            continue
        if abs(abs(float(amount)) - float(intent["ask_price"])) > 0.01:
            continue
        if row.get("contract_id") is None:
            continue
        matches.append(row)
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
