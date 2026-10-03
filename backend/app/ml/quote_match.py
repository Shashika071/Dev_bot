"""Match training entry times to nearest prior live quotes for economic backtest."""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd
import structlog

logger = structlog.get_logger(__name__)

DEFAULT_FALLBACK_BREAKEVEN = 0.956


def match_quotes_to_entries(
    entry_epochs: np.ndarray,
    quotes_df: pd.DataFrame,
    *,
    direction: str,
    max_age_seconds: int = 120,
    fallback_breakeven: float = DEFAULT_FALLBACK_BREAKEVEN,
) -> dict:
    """
    For each entry epoch, find the latest quote at or before entry for `direction`.

    quotes_df columns expected: quote_epoch (or spot_time), barrier_direction,
    ask_price, payout, breakeven_prob (optional).

    Returns arrays aligned to entry_epochs plus a matched mask.
    """
    n = len(entry_epochs)
    breakevens = np.full(n, fallback_breakeven, dtype=float)
    prices = np.full(n, np.nan)
    payouts = np.full(n, np.nan)
    matched = np.zeros(n, dtype=bool)

    if quotes_df is None or quotes_df.empty:
        return {
            "breakeven_probs": breakevens,
            "quote_prices": prices,
            "quote_payouts": payouts,
            "matched": matched,
            "match_rate": 0.0,
            "used_fallback": True,
        }

    q = quotes_df.copy()
    if "barrier_direction" in q.columns:
        q = q[q["barrier_direction"].astype(str).str.lower() == direction.lower()]
    if q.empty:
        return {
            "breakeven_probs": breakevens,
            "quote_prices": prices,
            "quote_payouts": payouts,
            "matched": matched,
            "match_rate": 0.0,
            "used_fallback": True,
        }

    epoch_col = "quote_epoch" if "quote_epoch" in q.columns else "spot_time"
    q = q.dropna(subset=[epoch_col, "ask_price", "payout"])
    q = q.sort_values(epoch_col)
    q_epochs = q[epoch_col].astype(np.int64).values
    q_ask = q["ask_price"].astype(float).values
    q_pay = q["payout"].astype(float).values
    if "breakeven_prob" in q.columns:
        q_be = q["breakeven_prob"].astype(float).values
    else:
        with np.errstate(divide="ignore", invalid="ignore"):
            q_be = np.where(q_pay > 0, q_ask / q_pay, fallback_breakeven)

    for i, ee in enumerate(np.asarray(entry_epochs, dtype=np.int64)):
        # rightmost quote_epoch <= entry
        idx = np.searchsorted(q_epochs, ee, side="right") - 1
        if idx < 0:
            continue
        age = int(ee) - int(q_epochs[idx])
        if age < 0 or age > int(max_age_seconds):
            continue
        if q_pay[idx] <= 0 or q_ask[idx] <= 0:
            continue
        matched[i] = True
        prices[i] = q_ask[idx]
        payouts[i] = q_pay[idx]
        be = float(q_be[idx])
        if not np.isfinite(be) or be <= 0 or be >= 1:
            be = float(q_ask[idx] / q_pay[idx])
        breakevens[i] = be

    rate = float(matched.mean()) if n else 0.0
    logger.info("quote_match_complete", n=n, match_rate=rate, direction=direction)
    return {
        "breakeven_probs": breakevens,
        "quote_prices": prices,
        "quote_payouts": payouts,
        "matched": matched,
        "match_rate": rate,
        "used_fallback": rate < 1.0,
    }
