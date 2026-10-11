"""Ask, payout, break-even, and expected value.

`total_payout` is the amount returned on a win, including the stake.
Net profit on a win is total_payout - ask_price. A loss costs ask_price.
"""

from __future__ import annotations

import math


def break_even_probability(ask_price: float, total_payout: float) -> float:
    if ask_price <= 0 or total_payout <= 0:
        raise ValueError("ask price and total payout must be positive")
    if ask_price > total_payout:
        raise ValueError("ask price cannot exceed total payout")
    return ask_price / total_payout


def estimated_net_ev(calibrated_probability: float, ask_price: float, total_payout: float) -> float:
    if not 0 <= calibrated_probability <= 1:
        raise ValueError("probability must be between 0 and 1")
    return calibrated_probability * total_payout - ask_price


def net_profit(won: bool, ask_price: float, total_payout: float) -> float:
    return (total_payout - ask_price) if won else -ask_price


def stake_to_cover(
    *,
    loss: float,
    target_profit: float,
    ask_price: float,
    total_payout: float,
    floor: float,
    ceiling: float,
) -> float | None:
    """Smallest stake whose win profit covers this run's losses plus the target.

    `total_payout` includes the stake. Profit is total_payout minus the ask.
    The stake stays at `floor` while that profit is already enough.
    """
    if loss < 0 or target_profit < 0 or floor <= 0 or ceiling < floor:
        return None
    if ask_price <= 0 or total_payout <= ask_price:
        return None
    profit_per_stake = (total_payout - ask_price) / ask_price
    if profit_per_stake <= 0:
        return None
    raw = (loss + target_profit) / profit_per_stake
    stake = max(floor, math.ceil(raw * 100 - 1e-9) / 100)
    if stake > ceiling + 1e-9:
        return None
    return stake
