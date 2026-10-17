"""Match training entry times to nearest prior live quotes for economic backtest."""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd
import structlog

logger = structlog.get_logger(__name__)

DEFAULT_FALLBACK_BREAKEVEN = 0.956


def duration_to_seconds(value: object, unit: object) -> int | None:
    """Convert quote duration fields to seconds. Returns None when unknown."""
    try:
        amount = int(value)
    except (TypeError, ValueError):
        return None
    u = str(unit or "s").strip().lower()
    if u in ("s", "sec", "secs", "second", "seconds"):
        return amount
    if u in ("m", "min", "mins", "minute", "minutes"):
        return amount * 60
    if u in ("h", "hr", "hour", "hours"):
        return amount * 3600
    if u in ("d", "day", "days"):
        return amount * 86400
    return None


def parse_barrier_distance(barrier_input: object) -> float | None:
    """Parse '+0.09' / '-0.08' / '0.3' into an absolute price-point distance."""
    if barrier_input is None:
        return None
    text = str(barrier_input).strip().replace(" ", "")
    if not text:
        return None
    try:
        return abs(float(text))
    except ValueError:
        return None


def filter_quotes_for_contract(
    quotes_df: pd.DataFrame,
    *,
    direction: str,
    barrier_distance: float | None = None,
    duration_seconds: int | None = None,
    contract_type: str | None = "ONETOUCH",
    currency: str | None = None,
    barrier_tolerance: float = 1e-6,
) -> pd.DataFrame:
    """Keep only quotes that match the trained contract identity."""
    if quotes_df is None or quotes_df.empty:
        return pd.DataFrame()
    q = quotes_df.copy()
    if "barrier_direction" in q.columns:
        q = q[q["barrier_direction"].astype(str).str.lower() == str(direction).lower()]
    if contract_type and "contract_type" in q.columns:
        q = q[q["contract_type"].astype(str).str.upper() == str(contract_type).upper()]
    if currency and "currency" in q.columns:
        q = q[q["currency"].astype(str).str.upper() == str(currency).upper()]

    if barrier_distance is not None:
        if "barrier_distance" in q.columns:
            distances = pd.to_numeric(q["barrier_distance"], errors="coerce")
        elif "barrier_input" in q.columns:
            distances = q["barrier_input"].map(parse_barrier_distance)
        else:
            return pd.DataFrame()
        q = q[(distances - float(barrier_distance)).abs() <= float(barrier_tolerance)]

    if duration_seconds is not None:
        if "duration_seconds" in q.columns:
            seconds = pd.to_numeric(q["duration_seconds"], errors="coerce")
        elif "duration_value" in q.columns:
            units = q["duration_unit"] if "duration_unit" in q.columns else "s"
            seconds = [
                duration_to_seconds(value, unit)
                for value, unit in zip(q["duration_value"], units)
            ]
            seconds = pd.Series(seconds, index=q.index)
        else:
            return pd.DataFrame()
        q = q[seconds == int(duration_seconds)]

    return q.reset_index(drop=True)


def match_quotes_to_entries(
    entry_epochs: np.ndarray,
    quotes_df: pd.DataFrame,
    *,
    direction: str,
    barrier_distance: float | None = None,
    duration_seconds: int | None = None,
    contract_type: str | None = "ONETOUCH",
    currency: str | None = None,
    max_age_seconds: int = 120,
    fallback_breakeven: float = DEFAULT_FALLBACK_BREAKEVEN,
) -> dict:
    """
    For each entry epoch, find the latest compatible quote at or before entry.

    Compatible means same direction and, when provided, same barrier distance,
    duration, contract type and currency. Wrong-contract quotes never match.
    """
    n = len(entry_epochs)
    breakevens = np.full(n, fallback_breakeven, dtype=float)
    prices = np.full(n, np.nan)
    payouts = np.full(n, np.nan)
    matched = np.zeros(n, dtype=bool)

    empty = {
        "breakeven_probs": breakevens,
        "quote_prices": prices,
        "quote_payouts": payouts,
        "matched": matched,
        "match_rate": 0.0,
        "used_fallback": True,
    }
    if quotes_df is None or quotes_df.empty:
        return empty

    q = filter_quotes_for_contract(
        quotes_df,
        direction=direction,
        barrier_distance=barrier_distance,
        duration_seconds=duration_seconds,
        contract_type=contract_type,
        currency=currency,
    )
    if q.empty:
        return empty

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
    logger.info(
        "quote_match_complete",
        n=n,
        match_rate=rate,
        direction=direction,
        barrier_distance=barrier_distance,
        duration_seconds=duration_seconds,
        contract_type=contract_type,
    )
    return {
        "breakeven_probs": breakevens,
        "quote_prices": prices,
        "quote_payouts": payouts,
        "matched": matched,
        "match_rate": rate,
        "used_fallback": rate < 1.0,
    }
