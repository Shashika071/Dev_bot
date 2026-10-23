"""Forward paper outcomes from observed R_100 ticks. This module places no orders."""
from __future__ import annotations

import math

from app.trading_ml.evaluation.metrics import max_drawdown, wilson_interval

STRATEGY_VERSION = "frama-range-1m-v1"
QUOTE_GRACE_SECONDS = 12


def clean_ticks(rows: list) -> list[dict]:
    """Keep one finite print per second. Later duplicates replace earlier ones."""
    kept: dict[int, float] = {}
    if not isinstance(rows, list):
        return []
    for row in rows:
        if not isinstance(row, dict):
            continue
        epoch = row.get("epoch")
        quote = row.get("quote")
        if isinstance(epoch, bool) or isinstance(quote, bool):
            continue
        try:
            epoch_value = float(epoch)
            price = float(quote)
        except (TypeError, ValueError):
            continue
        if not epoch_value.is_integer() or not math.isfinite(price) or price <= 0 or epoch_value < 0:
            continue
        kept[int(epoch_value)] = price
    return [{"epoch": epoch, "quote": kept[epoch]} for epoch in sorted(kept)]


def _first_quote(path: list[dict], start: float, end: float) -> dict | None:
    for row in path:
        epoch = row.get("epoch")
        if isinstance(epoch, (int, float)) and not isinstance(epoch, bool) and start <= float(epoch) <= end:
            return row
    return None


def tick_outcome(
    path: list[dict],
    *,
    requested_entry: float,
    duration: int,
    direction: str,
    net_payout: float | None,
) -> dict:
    """Settle from the first tick at entry and the first tick at expiry.

    A missing quote stays unresolved and does not become a win or a loss.
    """
    entry = _first_quote(path, requested_entry, requested_entry + QUOTE_GRACE_SECONDS)
    expiry_at = requested_entry + duration
    expiry = _first_quote(path, expiry_at, expiry_at + QUOTE_GRACE_SECONDS)
    base = {"source": "forward_tick_paper", "profit_per_unit_stake": None}
    if entry is None or expiry is None or direction not in {"RISE", "FALL"}:
        return {**base, "outcome": "unresolved"}
    entry_quote = float(entry["quote"])
    expiry_quote = float(expiry["quote"])
    won = expiry_quote > entry_quote if direction == "RISE" else expiry_quote < entry_quote
    profit = None
    if won and isinstance(net_payout, (int, float)) and not isinstance(net_payout, bool) and math.isfinite(net_payout):
        profit = float(net_payout)
    elif not won:
        profit = -1.0
    return {
        **base,
        "outcome": "win" if won else "loss",
        "entry_epoch": int(entry["epoch"]),
        "entry_quote": entry_quote,
        "expiry_epoch": int(expiry["epoch"]),
        "expiry_quote": expiry_quote,
        "profit_per_unit_stake": profit,
    }


def summarize(items: list[dict]) -> dict:
    pending = sum(1 for row in items if row.get("outcome") == "pending")
    unresolved = sum(1 for row in items if row.get("outcome") == "unresolved")
    resolved = [row for row in items if row.get("outcome") in {"win", "loss"}]
    wins = sum(1 for row in resolved if row["outcome"] == "win")
    lower, upper = wilson_interval(wins, len(resolved))
    profits = [
        float(row["profit_per_unit_stake"])
        for row in resolved
        if isinstance(row.get("profit_per_unit_stake"), (int, float))
        and not isinstance(row.get("profit_per_unit_stake"), bool)
        and math.isfinite(row["profit_per_unit_stake"])
    ]
    return {
        "recorded": len(items),
        "resolved": len(resolved),
        "pending": pending,
        "unresolved": unresolved,
        "win_rate": None if not resolved else wins / len(resolved),
        "wilson_lower": lower,
        "wilson_upper": upper,
        "priced": len(profits),
        "profit_per_unit_stake": None if not profits else float(sum(profits)),
        "max_drawdown_units": max_drawdown(profits),
    }
