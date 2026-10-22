"""R_100 Rise/Fall research signals.

This package learns from stored Volatility 100 candles and explains a
RISE, FALL, or NO TRADE decision. It does not place orders. The existing
one-minute FRAMA bot remains a separate path.
"""

from app.trading_ml.config import CONTRACT, DEFAULT_DURATION_SECONDS, SYMBOL

__all__ = ["CONTRACT", "DEFAULT_DURATION_SECONDS", "SYMBOL"]
