"""
Strategy B: Donchian Breakout Strategy

Compares current price with highs/lows of prior completed periods.
20-period channel is a starting candidate. Current candle is excluded.
"""

from typing import Optional
import pandas as pd
import numpy as np

from app.strategies.base import BaseStrategy, StrategySignal


class DonchianBreakoutStrategy(BaseStrategy):
    """
    Generates touch signals based on Donchian channel breakouts.
    Price above upper channel → upper touch candidate.
    Price below lower channel → lower touch candidate.
    """

    name = "donchian_breakout"
    description = "Donchian channel breakout with prior-period exclusion"

    def __init__(self, period: int = 20, confirmation_ticks: int = 3):
        self.params = {
            "period": period,
            "confirmation_ticks": confirmation_ticks,
        }

    def evaluate(
        self,
        features_df: pd.DataFrame,
        current_price: float,
        barrier_distance: float,
    ) -> Optional[StrategySignal]:
        if features_df.empty or len(features_df) < self.params["confirmation_ticks"]:
            return None

        recent = features_df.tail(self.params["confirmation_ticks"])
        latest = features_df.iloc[-1]

        breakout_state = latest.get("donchian_breakout_state", 0)
        dist_upper = latest.get("dist_to_donchian_upper", np.nan)
        dist_lower = latest.get("dist_to_donchian_lower", np.nan)

        if pd.isna(breakout_state):
            return None

        direction = None
        confidence = 0.0
        explanation = ""

        # Confirm breakout across multiple ticks for reliability
        recent_states = recent.get("donchian_breakout_state", pd.Series())
        if recent_states.empty:
            return None

        # Upper breakout
        if breakout_state > 0 and (recent_states > 0).sum() >= self.params["confirmation_ticks"]:
            direction = "upper"
            confidence = min(0.65, 0.4 + abs(dist_upper) * 10 if not pd.isna(dist_upper) else 0.4)
            explanation = (
                f"Price above Donchian upper channel for "
                f"{self.params['confirmation_ticks']} ticks, "
                f"distance to upper: {dist_upper:.4%}"
            )

        # Lower breakout
        elif breakout_state < 0 and (recent_states < 0).sum() >= self.params["confirmation_ticks"]:
            direction = "lower"
            confidence = min(0.65, 0.4 + abs(dist_lower) * 10 if not pd.isna(dist_lower) else 0.4)
            explanation = (
                f"Price below Donchian lower channel for "
                f"{self.params['confirmation_ticks']} ticks, "
                f"distance to lower: {dist_lower:.4%}"
            )

        if direction is None:
            return None

        return StrategySignal(
            direction=direction,
            confidence_raw=confidence,
            strategy_name=self.name,
            strategy_params=self.params,
            features={
                "donchian_breakout_state": float(breakout_state),
                "dist_to_donchian_upper": float(dist_upper) if not pd.isna(dist_upper) else 0,
                "dist_to_donchian_lower": float(dist_lower) if not pd.isna(dist_lower) else 0,
            },
            explanation=explanation,
        )
