"""
Strategy C: Bollinger Bands + RSI Reversal Strategy

Measures distance from average and bands, includes RSI for reversal conditions.
Conventional settings are hypotheses to test, not assumed profitable.
"""

from typing import Optional
import pandas as pd
import numpy as np

from app.strategies.base import BaseStrategy, StrategySignal


class BollingerRSIStrategy(BaseStrategy):
    """
    Generates touch signals based on Bollinger Band extremes with RSI confirmation.
    Price near upper band + RSI overbought → reversal (lower touch) or continuation (upper touch).
    Price near lower band + RSI oversold → reversal (upper touch) or continuation (lower touch).
    """

    name = "bbands_rsi"
    description = "Bollinger Bands + RSI reversal/continuation detection"

    def __init__(
        self,
        bb_period: int = 20,
        bb_std: float = 2.0,
        rsi_period: int = 14,
        rsi_overbought: float = 70,
        rsi_oversold: float = 30,
        bb_extreme_threshold: float = 0.9,  # Position > 0.9 = near upper band
        mode: str = "reversal",  # "reversal" or "continuation"
    ):
        self.params = {
            "bb_period": bb_period,
            "bb_std": bb_std,
            "rsi_period": rsi_period,
            "rsi_overbought": rsi_overbought,
            "rsi_oversold": rsi_oversold,
            "bb_extreme_threshold": bb_extreme_threshold,
            "mode": mode,
        }

    def evaluate(
        self,
        features_df: pd.DataFrame,
        current_price: float,
        barrier_distance: float,
    ) -> Optional[StrategySignal]:
        if features_df.empty:
            return None

        latest = features_df.iloc[-1]

        bb_pos = latest.get("bb_position", np.nan)
        rsi_val = latest.get("rsi_14", np.nan)
        bb_bw = latest.get("bb_bandwidth", np.nan)

        if pd.isna(bb_pos) or pd.isna(rsi_val):
            return None

        direction = None
        confidence = 0.0
        explanation = ""

        threshold = self.params["bb_extreme_threshold"]
        overbought = self.params["rsi_overbought"]
        oversold = self.params["rsi_oversold"]

        if self.params["mode"] == "reversal":
            # Near upper band + overbought RSI → expect reversal → lower touch
            if bb_pos > threshold and rsi_val > overbought:
                direction = "lower"
                confidence = min(0.6, 0.35 + (bb_pos - threshold) + (rsi_val - overbought) / 100)
                explanation = (
                    f"Price near upper Bollinger Band (position={bb_pos:.2f}), "
                    f"RSI overbought ({rsi_val:.1f}>{overbought}), "
                    f"reversal expected"
                )

            # Near lower band + oversold RSI → expect reversal → upper touch
            elif bb_pos < (1 - threshold) and rsi_val < oversold:
                direction = "upper"
                confidence = min(0.6, 0.35 + (1 - threshold - bb_pos) + (oversold - rsi_val) / 100)
                explanation = (
                    f"Price near lower Bollinger Band (position={bb_pos:.2f}), "
                    f"RSI oversold ({rsi_val:.1f}<{oversold}), "
                    f"reversal expected"
                )

        elif self.params["mode"] == "continuation":
            # Strong breakout above upper band → continuation → upper touch
            if bb_pos > 1.0 and rsi_val > 50:
                direction = "upper"
                confidence = min(0.6, 0.35 + (bb_pos - 1.0) * 2)
                explanation = (
                    f"Price above upper Bollinger Band (position={bb_pos:.2f}), "
                    f"RSI bullish ({rsi_val:.1f}), continuation expected"
                )

            # Strong breakout below lower band → continuation → lower touch
            elif bb_pos < 0.0 and rsi_val < 50:
                direction = "lower"
                confidence = min(0.6, 0.35 + abs(bb_pos) * 2)
                explanation = (
                    f"Price below lower Bollinger Band (position={bb_pos:.2f}), "
                    f"RSI bearish ({rsi_val:.1f}), continuation expected"
                )

        if direction is None:
            return None

        return StrategySignal(
            direction=direction,
            confidence_raw=confidence,
            strategy_name=self.name,
            strategy_params=self.params,
            features={
                "bb_position": float(bb_pos),
                "rsi_14": float(rsi_val),
                "bb_bandwidth": float(bb_bw) if not pd.isna(bb_bw) else 0,
            },
            explanation=explanation,
        )
