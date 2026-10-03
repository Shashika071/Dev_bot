"""
Expected-value filter — only allows signals where held-out evidence
supports a conservative edge over the live quote break-even.

An alert is only allowed when:
1. Conservative empirical probability (Wilson CI lower) exceeds breakeven + margin
2. Sufficient held-out samples exist near the predicted probability
3. Model metadata marks demonstrated edge (unless disabled)
4. EV using conservative probability is positive
"""

from dataclasses import dataclass
from typing import Optional

import structlog

from app.config import settings

logger = structlog.get_logger(__name__)


@dataclass
class EVFilterResult:
    """Result of expected-value filtering."""
    passes: bool
    purchase_price: float
    total_payout: float
    breakeven_probability: float
    calibrated_probability: float
    conservative_probability: float
    ev_net: float
    margin: float
    min_required_margin: float
    reason: str


class EVFilter:
    """Filters signals based on expected value and held-out evidence."""

    def __init__(
        self,
        min_ev_margin: float | None = None,
        min_samples_in_range: int | None = None,
        require_demonstrated_edge: bool = True,
    ):
        self.min_ev_margin = (
            float(settings.min_ev_margin) if min_ev_margin is None else float(min_ev_margin)
        )
        self.min_samples_in_range = (
            int(settings.min_calibration_samples)
            if min_samples_in_range is None
            else int(min_samples_in_range)
        )
        self.require_demonstrated_edge = require_demonstrated_edge

    def evaluate(
        self,
        calibrated_prob: float,
        purchase_price: float,
        total_payout: float,
        model_has_edge: bool = False,
        calibration_sample_count: int = 0,
        empirical_hit_rate: Optional[float] = None,
        empirical_ci_lower: Optional[float] = None,
    ) -> EVFilterResult:
        """
        Evaluate whether a signal passes the EV filter.

        Prefer conservative empirical CI lower bound when available; otherwise
        fall back to calibrated_prob (still requiring sample count).
        """
        if total_payout <= 0:
            return EVFilterResult(
                passes=False,
                purchase_price=purchase_price,
                total_payout=total_payout,
                breakeven_probability=1.0,
                calibrated_probability=calibrated_prob,
                conservative_probability=0.0,
                ev_net=-purchase_price,
                margin=0.0,
                min_required_margin=self.min_ev_margin,
                reason="Invalid payout (zero or negative)",
            )

        breakeven_prob = purchase_price / total_payout
        conservative = (
            float(empirical_ci_lower)
            if empirical_ci_lower is not None
            else float(calibrated_prob)
        )
        ev_net = conservative * total_payout - purchase_price
        margin = conservative - breakeven_prob

        if self.require_demonstrated_edge and not model_has_edge:
            return EVFilterResult(
                passes=False,
                purchase_price=purchase_price,
                total_payout=total_payout,
                breakeven_probability=breakeven_prob,
                calibrated_probability=calibrated_prob,
                conservative_probability=conservative,
                ev_net=ev_net,
                margin=margin,
                min_required_margin=self.min_ev_margin,
                reason="No demonstrated edge. Model has not been validated to show advantage.",
            )

        if calibration_sample_count < self.min_samples_in_range:
            return EVFilterResult(
                passes=False,
                purchase_price=purchase_price,
                total_payout=total_payout,
                breakeven_probability=breakeven_prob,
                calibrated_probability=calibrated_prob,
                conservative_probability=conservative,
                ev_net=ev_net,
                margin=margin,
                min_required_margin=self.min_ev_margin,
                reason=(
                    f"Insufficient calibration evidence: {calibration_sample_count} samples "
                    f"< {self.min_samples_in_range} required in this probability range"
                ),
            )

        if ev_net <= 0:
            return EVFilterResult(
                passes=False,
                purchase_price=purchase_price,
                total_payout=total_payout,
                breakeven_probability=breakeven_prob,
                calibrated_probability=calibrated_prob,
                conservative_probability=conservative,
                ev_net=ev_net,
                margin=margin,
                min_required_margin=self.min_ev_margin,
                reason=f"Negative expected value: EV={ev_net:.4f}",
            )

        if margin < self.min_ev_margin:
            return EVFilterResult(
                passes=False,
                purchase_price=purchase_price,
                total_payout=total_payout,
                breakeven_probability=breakeven_prob,
                calibrated_probability=calibrated_prob,
                conservative_probability=conservative,
                ev_net=ev_net,
                margin=margin,
                min_required_margin=self.min_ev_margin,
                reason=(
                    f"Insufficient margin: {margin:.4f} < {self.min_ev_margin:.4f} required. "
                    f"Conservative={conservative:.4f}, breakeven={breakeven_prob:.4f}"
                ),
            )

        return EVFilterResult(
            passes=True,
            purchase_price=purchase_price,
            total_payout=total_payout,
            breakeven_probability=breakeven_prob,
            calibrated_probability=calibrated_prob,
            conservative_probability=conservative,
            ev_net=ev_net,
            margin=margin,
            min_required_margin=self.min_ev_margin,
            reason=(
                f"Signal passes EV filter. "
                f"Margin={margin:.4f}, EV={ev_net:.4f}, "
                f"conservative={conservative:.4f} > breakeven={breakeven_prob:.4f}, "
                f"samples={calibration_sample_count}"
            ),
        )
