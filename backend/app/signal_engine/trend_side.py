"""Live trend side used to stop a saturated model from always buying Upper."""

from __future__ import annotations

from typing import Optional

import pandas as pd


def market_trend_side(features_df: Optional[pd.DataFrame]) -> Optional[str]:
    """
    Return 'upper' or 'lower' only when short trend agrees.

    Needs both EMA crossover and the 60-tick return on the same side.
    Flat or mixed trend returns None (no extra filter).
    """
    if features_df is None or getattr(features_df, "empty", True):
        return None
    row = features_df.iloc[-1]

    def _num(name: str) -> float:
        try:
            value = row.get(name, 0.0) if hasattr(row, "get") else row[name]
            if value is None or (isinstance(value, float) and value != value):
                return 0.0
            return float(value)
        except (KeyError, TypeError, ValueError):
            return 0.0

    cross = _num("ma_crossover")
    ret = _num("return_60")
    if cross > 0 and ret > 0:
        return "upper"
    if cross < 0 and ret < 0:
        return "lower"
    return None
