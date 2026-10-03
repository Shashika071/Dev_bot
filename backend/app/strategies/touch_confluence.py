"""
Touch-specific confluence strategy.

Unlike a close-direction strategy, this candidate asks whether the configured
barrier is reachable and whether short/medium momentum, trend, and expansion
agree on which barrier is more likely to be touched first.
"""

from typing import Optional

import numpy as np
import pandas as pd

from app.strategies.base import BaseStrategy, StrategySignal


class TouchConfluenceStrategy(BaseStrategy):
    """Require volatility reachability plus multi-window directional agreement."""

    name = "touch_confluence"
    description = (
        "Touch reachability with short/medium momentum, EMA trend, "
        "Bollinger/Donchian expansion, and RSI agreement"
    )

    def __init__(
        self,
        min_score: float = 5.0,
        min_reachability_ratio: float = 0.75,
        min_direction_gap: float = 1.5,
    ):
        self.params = {
            "min_score": min_score,
            "min_reachability_ratio": min_reachability_ratio,
            "min_direction_gap": min_direction_gap,
        }

    @staticmethod
    def _number(row: pd.Series, name: str, default: float = 0.0) -> float:
        value = row.get(name, default)
        return default if pd.isna(value) else float(value)

    def evaluate(
        self,
        features_df: pd.DataFrame,
        current_price: float,
        barrier_distance: float,
    ) -> Optional[StrategySignal]:
        if features_df.empty or current_price <= 0 or barrier_distance <= 0:
            return None

        latest = features_df.iloc[-1]
        range_60 = self._number(latest, "price_range_60")
        range_300 = self._number(latest, "price_range_300")
        observed_range = max(range_60, range_300)
        reachability = observed_range / barrier_distance

        # A nearby One-Touch barrier still needs evidence that recent movement
        # can cover most of the required distance. This is the first gate.
        if not np.isfinite(reachability) or (
            reachability < self.params["min_reachability_ratio"]
        ):
            return None

        upper_score = 0.0
        lower_score = 0.0
        upper_reasons: list[str] = []
        lower_reasons: list[str] = []

        crossover = self._number(latest, "ma_crossover")
        fast_slope = self._number(latest, "ma_fast_slope")
        slow_slope = self._number(latest, "ma_slow_slope")
        if crossover > 0 and fast_slope > 0 and slow_slope >= 0:
            upper_score += 2.0
            upper_reasons.append("EMA trend up")
        elif crossover < 0 and fast_slope < 0 and slow_slope <= 0:
            lower_score += 2.0
            lower_reasons.append("EMA trend down")

        returns = [
            self._number(latest, "return_10"),
            self._number(latest, "return_60"),
            self._number(latest, "return_300"),
        ]
        positive = sum(value > 0 for value in returns)
        negative = sum(value < 0 for value in returns)
        if positive >= 2:
            upper_score += 2.0
            upper_reasons.append(f"momentum up {positive}/3")
        if negative >= 2:
            lower_score += 2.0
            lower_reasons.append(f"momentum down {negative}/3")

        rsi = self._number(latest, "rsi_14", default=50.0)
        if 52.0 <= rsi <= 78.0:
            upper_score += 1.0
            upper_reasons.append(f"RSI {rsi:.1f}")
        elif 22.0 <= rsi <= 48.0:
            lower_score += 1.0
            lower_reasons.append(f"RSI {rsi:.1f}")

        bb_position = self._number(latest, "bb_position", default=0.5)
        if bb_position >= 0.55:
            upper_score += 1.0
            upper_reasons.append("Bollinger upper expansion")
        elif bb_position <= 0.45:
            lower_score += 1.0
            lower_reasons.append("Bollinger lower expansion")

        breakout = self._number(latest, "donchian_breakout_state")
        if breakout > 0:
            upper_score += 1.5
            upper_reasons.append("Donchian upper breakout")
        elif breakout < 0:
            lower_score += 1.5
            lower_reasons.append("Donchian lower breakout")

        # Reward volatility expansion, but never use it to choose direction.
        vol_now = self._number(latest, "realized_vol_60")
        vol_history = features_df.get("realized_vol_60")
        expansion = False
        if vol_history is not None:
            finite = pd.to_numeric(vol_history.tail(120), errors="coerce").dropna()
            if not finite.empty and vol_now > 0:
                expansion = vol_now >= float(finite.median())
        if expansion:
            upper_score += 0.5
            lower_score += 0.5

        best_score = max(upper_score, lower_score)
        score_gap = abs(upper_score - lower_score)
        if (
            best_score < self.params["min_score"]
            or score_gap < self.params["min_direction_gap"]
        ):
            return None

        if upper_score > lower_score:
            direction = "upper"
            reasons = upper_reasons
        else:
            direction = "lower"
            reasons = lower_reasons

        # This is a strategy score, not a calibrated probability.
        confidence = min(0.85, 0.45 + best_score * 0.05)
        explanation = (
            f"Touch confluence {direction}: score={best_score:.1f}, "
            f"opposite={min(upper_score, lower_score):.1f}, "
            f"recent range/barrier={reachability:.2f}; "
            + ", ".join(reasons)
        )

        return StrategySignal(
            direction=direction,
            confidence_raw=confidence,
            strategy_name=self.name,
            strategy_params=self.params,
            features={
                "upper_score": upper_score,
                "lower_score": lower_score,
                "reachability_ratio": reachability,
                "observed_range": observed_range,
                "barrier_distance": barrier_distance,
                "volatility_expanding": expansion,
            },
            explanation=explanation,
        )
