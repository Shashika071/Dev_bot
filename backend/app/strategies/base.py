"""
Base strategy interface for signal candidates.
Strategies are configurable research candidates, not assumed profitable.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional
import pandas as pd


@dataclass
class StrategySignal:
    """A candidate signal from a strategy (not yet validated by EV filter)."""
    direction: str  # "upper" or "lower"
    confidence_raw: float  # Strategy's raw confidence (0-1, NOT calibrated probability)
    strategy_name: str
    strategy_params: dict = field(default_factory=dict)
    features: dict = field(default_factory=dict)
    explanation: str = ""
    timestamp: Optional[pd.Timestamp] = None


class BaseStrategy(ABC):
    """
    Abstract base for signal-generation strategies.

    Each strategy examines current market features and optionally
    produces a candidate signal. The signal must then pass through
    the ML model and EV filter before becoming an actionable alert.

    Strategies are research candidates. Do not assume any strategy
    is profitable without validation evidence.
    """

    name: str = "base"
    description: str = ""
    params: dict = {}

    @abstractmethod
    def evaluate(
        self,
        features_df: pd.DataFrame,
        current_price: float,
        barrier_distance: float,
    ) -> Optional[StrategySignal]:
        """
        Evaluate current market conditions and optionally produce a signal.

        Args:
            features_df: Recent feature data (last N rows of computed features).
            current_price: Current spot price.
            barrier_distance: Configured barrier distance.

        Returns:
            StrategySignal if conditions are met, None otherwise.
        """
        pass

    def get_params(self) -> dict:
        """Return current parameter configuration."""
        return self.params.copy()

    def __repr__(self):
        return f"<Strategy({self.name}, params={self.params})>"
