"""Ask, payout, break-even, and expected value.

`total_payout` is the amount returned on a win, including the stake.
Net profit on a win is total_payout - ask_price. A loss costs ask_price.
"""

from __future__ import annotations


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
