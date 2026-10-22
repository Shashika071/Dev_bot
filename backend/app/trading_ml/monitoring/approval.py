"""Promotion uses the untouched test, never a training score."""
from __future__ import annotations

from app.trading_ml.config import MIN_SELECTED_FOR_PAPER


def judge_approval(test: dict, *, payout: float | None, min_selected: int) -> dict:
    reasons = []
    if not test.get("calibrated"):
        reasons.append("Probabilities are not calibrated on a held-out time slice.")
    log_loss = test.get("log_loss")
    baseline_log_loss = test.get("baseline_log_loss")
    if log_loss is None or baseline_log_loss is None or log_loss >= baseline_log_loss:
        reasons.append("Test log loss does not beat the base-rate baseline.")
    brier = test.get("brier")
    baseline_brier = test.get("baseline_brier")
    if brier is None or baseline_brier is None or brier >= baseline_brier:
        reasons.append("Test Brier score does not beat the base-rate baseline.")
    if test.get("auc") is None or float(test["auc"]) <= 0.5:
        reasons.append("Test ROC-AUC does not show ranking skill.")
    selected = int(test.get("selected") or 0)
    if selected < min_selected:
        reasons.append(f"Untouched test produced {selected} signals; {min_selected} are required.")
    if payout is None:
        reasons.append("No contract payout was supplied, so expected value cannot be tested.")
    elif test.get("ev_wilson_lower") is None or float(test["ev_wilson_lower"]) <= 0:
        reasons.append("The lower confidence bound on test expected value is not positive.")
    skilled = (
        log_loss is not None and baseline_log_loss is not None and log_loss < baseline_log_loss
        and test.get("auc") is not None and float(test["auc"]) > 0.52
        and selected >= MIN_SELECTED_FOR_PAPER
    )
    if not reasons:
        state = "SIGNAL_READY"
    elif skilled and payout is not None:
        state = "PAPER"
    else:
        state = "RESEARCH"
    return {"state": state, "reasons": reasons, "promoted": state == "SIGNAL_READY"}


def promotion_allowed(previous: dict | None, report: dict) -> tuple[bool, str]:
    """Training accuracy is ignored even if a caller puts it on the report."""
    if report.get("approval", {}).get("state") != "SIGNAL_READY":
        return False, "Challenger stays unpromoted because the untouched test did not pass the approval gates."
    new_loss = report.get("test", {}).get("log_loss")
    old_loss = None if not previous else previous.get("test_log_loss")
    if previous and previous.get("state") == "SIGNAL_READY" and old_loss is not None and new_loss is not None:
        if float(new_loss) >= float(old_loss):
            return False, "Challenger test log loss did not improve on the approved model."
    return True, "Challenger passed the chronological gates and can replace the active research model."
