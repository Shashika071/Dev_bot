"""Deriv Rise/Fall settlement. An equal price loses both directions."""
from __future__ import annotations

import math


def break_even(payout: float) -> float:
    if not math.isfinite(payout) or payout <= 0:
        raise ValueError("Payout must be a positive net profit per unit stake")
    return 1 / (1 + payout)


def expected_value(probability: float | None, payout: float | None) -> float | None:
    if probability is None or payout is None:
        return None
    if not math.isfinite(probability) or not 0 <= probability <= 1:
        raise ValueError("Win probability must be between 0 and 1")
    if not math.isfinite(payout) or payout <= 0:
        raise ValueError("Payout must be a positive net profit per unit stake")
    return float(probability * payout - (1 - probability))


def settle_contract(direction: str, entry_price: float, exit_price: float,
                    payout: float | None = None) -> dict:
    if direction not in {"RISE", "FALL"}:
        raise ValueError("Settlement direction must be RISE or FALL")
    entry = float(entry_price)
    exit_price = float(exit_price)
    if not all(math.isfinite(value) and value > 0 for value in (entry, exit_price)):
        raise ValueError("Settlement prices must be finite and positive")
    win = exit_price > entry if direction == "RISE" else exit_price < entry
    profit = None if payout is None else (float(payout) if win else -1.0)
    return {
        "direction": direction,
        "outcome": "win" if win else "loss",
        "tie": exit_price == entry,
        "profit_per_stake": profit,
        "payout": payout,
        "stake_policy": "fixed_unit",
    }
