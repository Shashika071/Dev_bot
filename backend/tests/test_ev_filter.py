import pytest
from app.signal_engine.ev_filter import EVFilter


def test_ev_filter_positive_margin():
    """Test when calibrated prob is significantly higher than breakeven."""
    filter = EVFilter(min_ev_margin=0.02, min_samples_in_range=50)
    
    # Stake $10, Payout $10.40 (4% net return)
    # Breakeven = 10 / 10.40 = ~96.15%
    # Calibrated = 99.00% -> EV Net = 0.99 * 10.40 - 10 = +0.296
    
    result = filter.evaluate(
        calibrated_prob=0.99,
        purchase_price=10.0,
        total_payout=10.40,
        model_has_edge=True,
        calibration_sample_count=100
    )
    
    assert result.passes is True
    assert result.margin > 0.02
    assert result.ev_net > 0


def test_ev_filter_negative_ev():
    """Test when calibrated prob is lower than breakeven."""
    filter = EVFilter(min_ev_margin=0.02, min_samples_in_range=50)
    
    # Breakeven = ~96.15%
    # Calibrated = 95.00%
    result = filter.evaluate(
        calibrated_prob=0.95,
        purchase_price=10.0,
        total_payout=10.40,
        model_has_edge=True,
        calibration_sample_count=100
    )
    
    assert result.passes is False
    assert "Negative expected value" in result.reason
    assert result.ev_net < 0


def test_ev_filter_insufficient_margin():
    """Test when EV is slightly positive but below the minimum required margin."""
    filter = EVFilter(min_ev_margin=0.02, min_samples_in_range=50)
    
    # Breakeven = ~96.15%
    # Calibrated = 97.00% -> Margin = 0.0085 (which is < 0.02)
    result = filter.evaluate(
        calibrated_prob=0.97,
        purchase_price=10.0,
        total_payout=10.40,
        model_has_edge=True,
        calibration_sample_count=100
    )
    
    assert result.passes is False
    assert "Insufficient margin" in result.reason


def test_ev_filter_requires_model_edge():
    """Test that a model without a demonstrated edge is rejected even with high probability."""
    filter = EVFilter(min_ev_margin=0.02, require_demonstrated_edge=True)
    
    result = filter.evaluate(
        calibrated_prob=0.99,
        purchase_price=10.0,
        total_payout=10.40,
        model_has_edge=False, # <-- Fails here
        calibration_sample_count=100
    )
    
    assert result.passes is False
    assert "No demonstrated edge" in result.reason


def test_ev_filter_insufficient_samples():
    """Test that high probability in a region with no calibration data is rejected."""
    filter = EVFilter(min_samples_in_range=50)
    
    result = filter.evaluate(
        calibrated_prob=0.99,
        purchase_price=10.0,
        total_payout=10.40,
        model_has_edge=True,
        calibration_sample_count=10 # <-- Less than 50
    )
    
    assert result.passes is False
    assert "Insufficient calibration evidence" in result.reason
