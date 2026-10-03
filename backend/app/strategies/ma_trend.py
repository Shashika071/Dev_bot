"""
Strategy A: Moving-Average Trend Strategy

Compares faster and slower EMAs with slope and crossover state.
6/21 periods are an initial experiment, not an optimal setting.
"""

from typing import Optional
import pandas as pd
import numpy as np

from app.strategies.base import BaseStrategy, StrategySignal


class MATrendStrategy(BaseStrategy):
    """
    Generates touch signals based on MA trend direction.
    When fast MA is above slow MA with positive slope → upper touch candidate.
    When fast MA is below slow MA with negative slope → lower touch candidate.
    """

    name = "ma_trend"
    description = "Moving-average trend with slope and crossover detection"

    def __init__(
        self,
        fast_period: int = 6,
        slow_period: int = 21,
        min_slope_threshold: float = 0.0001,
        min_return_confirmation: float = 0.0,
    ):
        self.params = {
            "fast_period": fast_period,
            "slow_period": slow_period,
            "min_slope_threshold": min_slope_threshold,
            "min_return_confirmation": min_return_confirmation,
        }

    def evaluate(
        self,
        features_df: pd.DataFrame,
        current_price: float,
        barrier_distance: float,
    ) -> Optional[StrategySignal]:
        if features_df.empty or len(features_df) < 2:
            return None

        latest = features_df.iloc[-1]

        # Required features
        crossover = latest.get("ma_crossover", 0)
        fast_slope = latest.get("ma_fast_slope", 0)
        slow_slope = latest.get("ma_slow_slope", 0)
        return_60 = latest.get("return_60", 0)

        if pd.isna(crossover) or pd.isna(fast_slope):
            return None

        direction = None
        confidence = 0.0
        explanation = ""

        # Upper touch: fast above slow, positive slopes, positive returns
        if (crossover > 0
                and fast_slope > self.params["min_slope_threshold"]
                and slow_slope >= 0):
            direction = "upper"
            # Raw confidence based on slope magnitude (NOT calibrated probability)
            confidence = min(0.7, 0.4 + abs(fast_slope) * 1000)
            explanation = (
                f"Fast MA above slow MA (crossover=+1), "
                f"fast slope={fast_slope:.6f}, slow slope={slow_slope:.6f}, "
                f"60s return={return_60:.4%}"
            )

        # Lower touch: fast below slow, negative slopes, negative returns
        elif (crossover < 0
              and fast_slope < -self.params["min_slope_threshold"]
              and slow_slope <= 0):
            direction = "lower"
            confidence = min(0.7, 0.4 + abs(fast_slope) * 1000)
            explanation = (
                f"Fast MA below slow MA (crossover=-1), "
                f"fast slope={fast_slope:.6f}, slow slope={slow_slope:.6f}, "
                f"60s return={return_60:.4%}"
            )

        if direction is None:
            return None

        return StrategySignal(
            direction=direction,
            confidence_raw=confidence,
            strategy_name=self.name,
            strategy_params=self.params,
            features={
                "ma_crossover": float(crossover),
                "ma_fast_slope": float(fast_slope),
                "ma_slow_slope": float(slow_slope),
                "return_60": float(return_60) if not pd.isna(return_60) else 0,
            },
            explanation=explanation,
        )
