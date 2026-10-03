"""Evidence-aware EV filtering uses conservative empirical CI."""

from app.signal_engine.ev_filter import EVFilter


def test_ev_uses_empirical_ci_lower():
    """Calibrated looks good but CI lower kills the edge."""
    filt = EVFilter(min_ev_margin=0.02, min_samples_in_range=50)
    # Breakeven ≈ 0.9615 for 10/10.40
    result = filt.evaluate(
        calibrated_prob=0.99,
        purchase_price=10.0,
        total_payout=10.40,
        model_has_edge=True,
        calibration_sample_count=80,
        empirical_hit_rate=0.97,
        empirical_ci_lower=0.90,  # below breakeven
    )
    assert result.passes is False
    assert result.conservative_probability == 0.90
    assert "Negative expected value" in result.reason or "Insufficient margin" in result.reason


def test_ev_passes_with_strong_ci():
    filt = EVFilter(min_ev_margin=0.02, min_samples_in_range=50)
    result = filt.evaluate(
        calibrated_prob=0.99,
        purchase_price=10.0,
        total_payout=10.40,
        model_has_edge=True,
        calibration_sample_count=80,
        empirical_hit_rate=0.995,
        empirical_ci_lower=0.99,
    )
    assert result.passes is True
    assert result.conservative_probability == 0.99
