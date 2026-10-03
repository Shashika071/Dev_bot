"""
Strategy registry — central place to manage all active strategies.
"""

from typing import Optional
from app.strategies.base import BaseStrategy, StrategySignal
from app.strategies.ma_trend import MATrendStrategy
from app.strategies.donchian import DonchianBreakoutStrategy
from app.strategies.bbands_rsi import BollingerRSIStrategy
from app.strategies.touch_confluence import TouchConfluenceStrategy
import pandas as pd


class StrategyRegistry:
    """
    Manages all configured strategies and runs them against market data.
    Strategies are research candidates — no requirement for all to agree.
    """

    def __init__(self):
        self._strategies: dict[str, BaseStrategy] = {}

    def register(self, strategy: BaseStrategy):
        self._strategies[strategy.name] = strategy

    def register_defaults(self):
        """Register default strategy candidates with initial parameters."""
        # Strategy A: MA Trend (6/21 as initial experiment)
        self.register(MATrendStrategy(fast_period=6, slow_period=21))

        # Strategy B: Donchian Breakout (20 period candidate)
        self.register(DonchianBreakoutStrategy(period=20))

        # Strategy C: Bollinger + RSI Reversal
        self.register(BollingerRSIStrategy(mode="reversal"))

        # Strategy C variant: Bollinger + RSI Continuation (registered under distinct name)
        cont = BollingerRSIStrategy(mode="continuation")
        cont.name = "bbands_rsi_continuation"
        self._strategies[cont.name] = cont

        # Touch-specific confirmation: reachability must agree with trend,
        # momentum, RSI, and breakout/expansion evidence.
        self.register(TouchConfluenceStrategy())

    def evaluate_all(
        self,
        features_df: pd.DataFrame,
        current_price: float,
        barrier_distance: float,
    ) -> list[StrategySignal]:
        """
        Run all strategies and collect candidate signals.
        Returns list of signals from strategies that triggered.
        """
        candidates = []
        for name, strategy in self._strategies.items():
            try:
                signal = strategy.evaluate(features_df, current_price, barrier_distance)
                if signal is not None:
                    candidates.append(signal)
            except Exception as e:
                pass  # Log but don't crash
        return candidates

    def get_strategy(self, name: str) -> Optional[BaseStrategy]:
        return self._strategies.get(name)

    def list_strategies(self) -> list[dict]:
        return [
            {
                "name": s.name,
                "description": s.description,
                "params": s.get_params(),
            }
            for s in self._strategies.values()
        ]
