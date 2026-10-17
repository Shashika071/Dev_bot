"""Fail-closed promotion rules for Touch models.

Passing these gates means the saved evidence cleared the stated checks.
It does not mean the model will make money.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GateThresholds:
    min_outer_folds: int = 4
    min_fold_win_fraction: float = 0.8
    min_quote_match_rate: float = 0.50
    min_selected_quote_match_rate: float = 0.80
    min_selected_signals: int = 30
    min_calibration_samples: int = 50
    max_unresolved_rate: float = 0.05
    edge_margin: float = 0.02
    brier_epsilon: float = 0.0
    require_positive_selected_net_return: bool = True


@dataclass
class GateDecision:
    passed: bool
    reasons: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        if self.passed:
            return self.reasons[0] if self.reasons else "Edge supported"
        return "; ".join(self.reasons) if self.reasons else "Promotion gates failed"


def evaluate_promotion_gates(
    *,
    selected_signal_count: int,
    selected_win_rate: float,
    selected_win_rate_ci_lower: float,
    mean_breakeven: float,
    quote_match_rate: float,
    selected_quote_match_rate: float,
    selected_matched_count: int,
    calibration_samples: int,
    calibration_both_classes: bool,
    unresolved_rate: float,
    eligible_folds: int,
    fold_win_fraction: float,
    lockbox_beats_baseline: bool,
    selected_mean_breakeven: float | None = None,
    quote_based_net_return: float | None = None,
    thresholds: GateThresholds | None = None,
) -> GateDecision:
    """Economic edge must be judged on the selected, quote-matched trades.

    `mean_breakeven` is retained for logging compatibility, but the CI
    comparison uses `selected_mean_breakeven` when provided. A strategy that
    loses money on selected matched stakes cannot pass.
    """
    limits = thresholds or GateThresholds()
    reasons: list[str] = []

    if float(quote_match_rate) < limits.min_quote_match_rate:
        reasons.append(
            "Insufficient quote coverage for economic edge: "
            f"match_rate={float(quote_match_rate):.2%} < {limits.min_quote_match_rate:.0%}"
        )
    if float(selected_quote_match_rate) < limits.min_selected_quote_match_rate:
        reasons.append(
            "Insufficient quote coverage on selected signals: "
            f"{float(selected_quote_match_rate):.2%} < {limits.min_selected_quote_match_rate:.0%}"
        )
    if int(selected_matched_count) < limits.min_selected_signals:
        reasons.append(
            "Insufficient matched selected signals: "
            f"{int(selected_matched_count)} < {limits.min_selected_signals}"
        )
    if int(selected_signal_count) < limits.min_selected_signals:
        reasons.append(
            "Insufficient selected test signals: "
            f"{int(selected_signal_count)} < {limits.min_selected_signals}"
        )
    if not calibration_both_classes:
        reasons.append("Calibration split does not contain both classes")
    if int(calibration_samples) < limits.min_calibration_samples:
        reasons.append(
            f"Calibration samples {int(calibration_samples)} < {limits.min_calibration_samples}"
        )
    if float(unresolved_rate) >= limits.max_unresolved_rate:
        reasons.append(
            f"Unresolved label rate {float(unresolved_rate):.2%} >= {limits.max_unresolved_rate:.0%}"
        )
    if int(eligible_folds) < limits.min_outer_folds:
        reasons.append(
            f"Walk-forward eligible folds {int(eligible_folds)} < {limits.min_outer_folds}"
        )
    if float(fold_win_fraction) < limits.min_fold_win_fraction:
        reasons.append(
            "Selected model does not beat the constant baseline on enough outer folds: "
            f"{float(fold_win_fraction):.0%} < {limits.min_fold_win_fraction:.0%}"
        )
    if not lockbox_beats_baseline:
        reasons.append("Lockbox Brier is not better than the constant baseline")

    economic_be = (
        float(selected_mean_breakeven)
        if selected_mean_breakeven is not None and np_isfinite(selected_mean_breakeven)
        else float(mean_breakeven)
    )
    threshold = economic_be + float(limits.edge_margin)
    if not np_isfinite(economic_be) or float(selected_win_rate_ci_lower) <= threshold:
        reasons.append(
            "No demonstrated edge: CI lower "
            f"{float(selected_win_rate_ci_lower):.4f} <= {threshold:.4f} "
            f"(selected matched quote BE={economic_be:.4f})"
        )

    if limits.require_positive_selected_net_return:
        if quote_based_net_return is None or not np_isfinite(quote_based_net_return):
            reasons.append(
                "Selected-trade payout-adjusted net return is unavailable; "
                "economic edge cannot be claimed"
            )
        elif float(quote_based_net_return) <= 0:
            reasons.append(
                "Selected matched trades have non-positive payout-adjusted net return: "
                f"{float(quote_based_net_return):.4f}"
            )

    if reasons:
        return GateDecision(False, reasons)
    return GateDecision(
        True,
        [
            "Edge supported: "
            f"selected_win_rate={float(selected_win_rate):.4f}, "
            f"CI_lower={float(selected_win_rate_ci_lower):.4f}, "
            f"selected_BE={economic_be:.4f}, "
            f"net_return={float(quote_based_net_return) if quote_based_net_return is not None else float('nan'):.4f}, "
            f"n={int(selected_signal_count)}, "
            f"quote_match={float(quote_match_rate):.2%}, "
            f"folds={int(eligible_folds)}"
        ],
    )


def np_isfinite(value: float) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return number == number and number not in (float("inf"), float("-inf"))
