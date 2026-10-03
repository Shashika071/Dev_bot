"""Payout break-even maths for EV filter."""

from app.signal_engine.ev_filter import EVFilter


def test_breakeven_four_percent_net():
    # S=10, P=10.40 => breakeven ≈ 0.961538
    filt = EVFilter(min_ev_margin=0.0, min_samples_in_range=1, require_demonstrated_edge=False)
    result = filt.evaluate(
        calibrated_prob=0.961538,
        purchase_price=10.0,
        total_payout=10.40,
        model_has_edge=True,
        calibration_sample_count=100,
        empirical_ci_lower=0.961538,
    )
    assert abs(result.breakeven_probability - (10.0 / 10.40)) < 1e-6
    assert abs(result.ev_net) < 1e-3


def test_positive_ev_requires_above_breakeven():
    filt = EVFilter(min_ev_margin=0.02, min_samples_in_range=50)
    result = filt.evaluate(
        calibrated_prob=0.99,
        purchase_price=10.0,
        total_payout=10.40,
        model_has_edge=True,
        calibration_sample_count=80,
        empirical_ci_lower=0.99,
    )
    assert result.passes is True
    assert result.ev_net > 0
