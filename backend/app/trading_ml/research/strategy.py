"""Choose a strategy from the current regime. No strategy means no trade."""
from __future__ import annotations

import math

from app.trading_ml.models.regime import regime_name


def _number(row, name: str) -> float:
    try:
        value = float(row[name])
    except (KeyError, TypeError, ValueError):
        return math.nan
    return value


def choose_strategy(row) -> dict:
    regime = regime_name(row)
    agreement = _number(row, "mtf_agreement")
    ret_5 = _number(row, "ret_5")
    rsi = _number(row, "rsi14")
    breakout_up = _number(row, "breakout_up") == 1
    breakout_down = _number(row, "breakout_down") == 1
    eligible: list[str] = []
    chosen = None
    direction = None
    if regime.startswith("trending") and math.isfinite(agreement) and abs(agreement) >= 0.5:
        direction = "RISE" if agreement > 0 else "FALL"
        chosen = "trend_following"
        eligible.append(chosen)
        if math.isfinite(ret_5) and ((direction == "RISE" and ret_5 > 0) or (direction == "FALL" and ret_5 < 0)):
            eligible.append("momentum")
    elif regime.endswith("high_volatility") and breakout_up != breakout_down:
        chosen = "breakout"
        direction = "RISE" if breakout_up else "FALL"
        eligible.append(chosen)
    elif regime.startswith("ranging") and not regime.endswith("high_volatility") and math.isfinite(rsi):
        if rsi <= 30:
            chosen, direction = "mean_reversion", "RISE"
            eligible.append(chosen)
        elif rsi >= 70:
            chosen, direction = "mean_reversion", "FALL"
            eligible.append(chosen)
    if chosen is None:
        reason = f"No trend, breakout or mean-reversion strategy is eligible in {regime}."
    else:
        reason = f"{chosen.replace('_', ' ')} is eligible for {direction} while the regime is {regime}."
    return {
        "regime": regime, "eligible": eligible, "chosen": chosen,
        "direction": direction, "reason": reason,
    }
