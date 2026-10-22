"""Fail-closed R_100 signal card."""
from __future__ import annotations

import math

from app.trading_ml.backtesting.contracts import expected_value
from app.trading_ml.config import CONTRACT, ENTRY_WINDOW_SECONDS, QUOTE_MAX_AGE_SECONDS, SYMBOL


def _side_probabilities(probability_up: float | None, tie_rate: float) -> tuple[float | None, float | None]:
    if probability_up is None or not math.isfinite(probability_up) or not 0 <= probability_up <= 1:
        return None, None
    if not math.isfinite(tie_rate) or not 0 <= tie_rate < 1:
        return None, None
    rise = probability_up * (1 - tie_rate)
    fall = (1 - probability_up) * (1 - tie_rate)
    return rise, fall


def decide_signal(*, probability_up: float | None, calibrated: bool, tie_rate: float,
                  strategy: dict, pattern: dict, volatility: dict, payout: float | None,
                  approval_state: str, quality_blocks: bool, quality_reason: str | None,
                  risk_blockers: list[str], duration_seconds: int, model_name: str | None,
                  ev_floor: float) -> dict:
    rise, fall = _side_probabilities(probability_up, tie_rate)
    candidate = None
    probability = None
    if rise is not None and fall is not None:
        candidate = "RISE" if rise >= fall else "FALL"
        probability = rise if candidate == "RISE" else fall
    value = expected_value(probability, payout)
    blockers = []
    if approval_state != "SIGNAL_READY":
        blockers.append(f"Model status is {approval_state}. User-facing signals stay off.")
    if not calibrated:
        blockers.append("The probability is not calibrated on a held-out time slice.")
    if payout is None:
        blockers.append("No Rise/Fall payout was supplied, so expected value cannot be calculated.")
    elif value is None or value < ev_floor:
        blockers.append("Expected value does not clear the minimum plus safety margin.")
    if quality_blocks:
        blockers.append(quality_reason or "Data quality blocked the signal.")
    if not volatility.get("available"):
        blockers.append(volatility.get("reason") or "Volatility forecast is unavailable.")
    chosen = strategy.get("direction")
    if not chosen or not strategy.get("chosen"):
        blockers.append(strategy.get("reason") or "No strategy is eligible.")
    elif candidate and chosen != candidate:
        blockers.append("The model direction and the eligible strategy disagree.")
    if pattern.get("opposes_candidate"):
        blockers.append(pattern.get("reason") or "Historical neighbors oppose the candidate.")
    blockers.extend(risk_blockers)
    trade_blockers = [item for item in blockers if not item.startswith("Model status is")]
    paper_ready = approval_state == "PAPER" and not trade_blockers
    final = candidate if candidate and not blockers else "NO TRADE"
    paper_direction = candidate if paper_ready and candidate else None
    reason = _reason(final, candidate, probability, value, payout, blockers, strategy, pattern)
    return {
        "market": SYMBOL,
        "contract": CONTRACT,
        "duration_seconds": int(duration_seconds),
        "direction": final,
        "candidate_direction": candidate,
        "paper_direction": paper_direction,
        "calibrated_win_probability": probability if calibrated else None,
        "raw_win_probability": probability,
        "market_regime": strategy.get("regime") or "unknown",
        "volatility": volatility.get("text") or "unavailable",
        "expected_value": value,
        "model_status": approval_state,
        "model_name": model_name,
        "final_signal": final,
        "reason": reason,
        "blockers": blockers,
        "strategy": strategy.get("chosen"),
        "places_trades": False,
        "payout": payout,
        "payout_source": None if payout is None else "user_supplied",
    }


def _reason(final, candidate, probability, value, payout, blockers, strategy, pattern) -> str:
    if final in {"RISE", "FALL"}:
        return (
            f"{final} clears the calibrated probability, {strategy.get('chosen', 'strategy')} eligibility, "
            f"payout and expected-value gates. {pattern.get('reason', '')}"
        ).strip()
    parts = []
    if candidate and probability is not None:
        payout_text = "no payout" if payout is None else f"payout {payout:.2f}"
        value_text = "unknown expected value" if value is None else f"expected value {value:.3f}"
        parts.append(f"Candidate {candidate} has win probability {probability:.3f}, {payout_text}, and {value_text}.")
    else:
        parts.append("No finite direction probability is available.")
    parts.append(blockers[0] if blockers else "A mandatory gate failed.")
    return " ".join(parts)


def apply_live_gates(card: dict, *, now: int, quote_epoch: int | None,
                     decision_epoch: int | None) -> dict:
    """A stored decision stops being actionable once its entry window or quote is stale."""
    updated = dict(card)
    blockers = list(updated.get("blockers") or [])
    stale_reason = None
    if decision_epoch is None or now < int(decision_epoch) or now - int(decision_epoch) > ENTRY_WINDOW_SECONDS:
        stale_reason = "The decision candle is outside the 20-second entry window."
    elif quote_epoch is None or now - int(quote_epoch) > QUOTE_MAX_AGE_SECONDS:
        stale_reason = "The live R_100 quote is missing or older than 30 seconds."
    updated["stale"] = stale_reason is not None
    updated["decision_epoch"] = decision_epoch
    if stale_reason and updated.get("final_signal") in {"RISE", "FALL"}:
        blockers.insert(0, stale_reason)
        updated["blockers"] = blockers
        updated["final_signal"] = "NO TRADE"
        updated["direction"] = "NO TRADE"
        updated["paper_direction"] = None
        updated["reason"] = f"{stale_reason} {updated.get('reason', '')}".strip()
    elif stale_reason:
        updated["paper_direction"] = None
        if stale_reason not in str(updated.get("reason")):
            updated["reason"] = f"{stale_reason} {updated.get('reason', '')}".strip()
    return updated
