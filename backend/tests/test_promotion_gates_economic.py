"""Selected-trade economics must reject a losing strategy."""

import numpy as np

from app.ml.evaluation import evaluate_model
from app.ml.validation_gates import GateThresholds, evaluate_promotion_gates


def test_economic_gate_rejects_losing_selected_trades():
    y = np.r_[np.ones(900), np.zeros(100), np.zeros(9000)]
    p = np.r_[np.full(1000, 0.99), np.full(9000, 0.1)]
    be = np.r_[np.full(1000, 0.95), np.full(9000, 0.2)]
    report = evaluate_model(
        "repro",
        "upper",
        y,
        p,
        breakeven_probs=be,
        quote_prices=np.ones(10000),
        quote_payouts=1.0 / be,
        quote_matched=np.ones(10000, dtype=bool),
        margin_over_breakeven=0.0,
    )
    assert report.selected_signal_count == 1000
    assert abs(float(report.selected_mean_breakeven) - 0.95) < 1e-9
    assert report.quote_based_net_return is not None
    assert report.quote_based_net_return < 0

    # Old defective comparison used overall mean BE (~0.275) and would pass.
    overall_be = float(be.mean())
    assert overall_be < 0.3

    gate = evaluate_promotion_gates(
        selected_signal_count=report.selected_signal_count,
        selected_win_rate=report.selected_win_rate,
        selected_win_rate_ci_lower=report.selected_win_rate_ci_lower,
        mean_breakeven=overall_be,
        selected_mean_breakeven=report.selected_mean_breakeven,
        quote_based_net_return=report.quote_based_net_return,
        quote_match_rate=1.0,
        selected_quote_match_rate=1.0,
        selected_matched_count=1000,
        calibration_samples=100,
        calibration_both_classes=True,
        unresolved_rate=0.0,
        eligible_folds=5,
        fold_win_fraction=1.0,
        lockbox_beats_baseline=True,
        thresholds=GateThresholds(edge_margin=0.02, min_selected_signals=30),
    )
    assert gate.passed is False
    assert any("non-positive payout-adjusted net return" in reason for reason in gate.reasons)


def test_economic_gate_uses_selected_breakeven_not_overall_average():
    y = np.r_[np.ones(900), np.zeros(100), np.zeros(9000)]
    p = np.r_[np.full(1000, 0.99), np.full(9000, 0.1)]
    be = np.r_[np.full(1000, 0.95), np.full(9000, 0.2)]
    report = evaluate_model(
        "repro",
        "upper",
        y,
        p,
        breakeven_probs=be,
        quote_prices=np.ones(10000),
        quote_payouts=1.0 / be,
        quote_matched=np.ones(10000, dtype=bool),
        margin_over_breakeven=0.0,
    )
    # Force a positive net return so only the BE comparison is tested.
    gate = evaluate_promotion_gates(
        selected_signal_count=report.selected_signal_count,
        selected_win_rate=report.selected_win_rate,
        selected_win_rate_ci_lower=report.selected_win_rate_ci_lower,
        mean_breakeven=float(be.mean()),
        selected_mean_breakeven=report.selected_mean_breakeven,
        quote_based_net_return=10.0,
        quote_match_rate=1.0,
        selected_quote_match_rate=1.0,
        selected_matched_count=1000,
        calibration_samples=100,
        calibration_both_classes=True,
        unresolved_rate=0.0,
        eligible_folds=5,
        fold_win_fraction=1.0,
        lockbox_beats_baseline=True,
        thresholds=GateThresholds(edge_margin=0.02, min_selected_signals=30),
    )
    assert gate.passed is False
    assert any("selected matched quote BE=0.9500" in reason for reason in gate.reasons)
