"""
Touch outcome labeling — determines whether a barrier was touched
during a contract window using actual tick data.

IMPORTANT:
- Labels are determined by actual ticks, NOT candle closing prices.
- Upper and lower touches are separate events — both can happen in one window.
- A pre-entry touch is NOT a winning contract.
- Windows with insufficient tick data are unresolved (excluded from training).
- Optional manual entry delay shifts the observation window start.
"""

import numpy as np
import pandas as pd
import structlog

logger = structlog.get_logger(__name__)

# Default minimum ticks in outcome window; trainer uses a stricter value.
MIN_WINDOW_TICKS = 1


def label_touch_outcomes(
    ticks_df: pd.DataFrame,
    entry_times: pd.Series,
    duration_seconds: int,
    barrier_distance: float,
    barrier_direction: str = "upper",
    barrier_type: str = "relative",
    entry_delay_seconds: int = 0,
    min_window_ticks: int = MIN_WINDOW_TICKS,
) -> pd.DataFrame:
    """
    For each entry time, determine whether the barrier was touched
    within the contract window using actual tick data.

    Barrier distance for relative contracts is in **price points**
    (same units as Deriv relative barrier input, e.g. 0.09 or 0.2),
    not percent.

    Returns columns including:
      resolved (bool) — False when the window lacks enough ticks
      touched (bool|NA) — only meaningful when resolved=True
    """
    if ticks_df.empty or entry_times.empty:
        return pd.DataFrame()

    ticks_sorted = ticks_df.sort_values("tick_time").reset_index(drop=True)
    tick_prices = ticks_sorted["quote"].values
    tick_epochs = ticks_sorted["epoch"].values

    results = []

    for entry_time in entry_times:
        entry_epoch = int(entry_time.timestamp()) if hasattr(entry_time, "timestamp") else int(entry_time)
        # Manual entry delay: observation starts after delay, still ends at entry+duration
        window_start_epoch = entry_epoch + max(0, int(entry_delay_seconds))
        window_end_epoch = entry_epoch + duration_seconds
        if window_start_epoch >= window_end_epoch:
            results.append(_unresolved_row(entry_time, barrier_direction, reason="delay_exceeds_duration"))
            continue

        entry_mask = ticks_sorted["epoch"] <= entry_epoch
        if not entry_mask.any():
            results.append(_unresolved_row(entry_time, barrier_direction, reason="no_entry_price"))
            continue
        entry_idx = entry_mask.values.nonzero()[0][-1]
        entry_price = float(tick_prices[entry_idx])

        if barrier_type == "relative":
            if barrier_direction == "upper":
                barrier_level = entry_price + barrier_distance
            else:
                barrier_level = entry_price - barrier_distance
        else:
            barrier_level = barrier_distance

        window_mask = (tick_epochs > window_start_epoch) & (tick_epochs <= window_end_epoch)
        window_prices = tick_prices[window_mask]
        ticks_in_window = int(window_mask.sum())

        if ticks_in_window < min_window_ticks:
            results.append({
                "entry_time": entry_time,
                "entry_price": entry_price,
                "barrier_level": barrier_level,
                "barrier_direction": barrier_direction,
                "barrier_distance": barrier_distance,
                "barrier_unit": "relative_price_points" if barrier_type == "relative" else "absolute_price",
                "duration_seconds": duration_seconds,
                "entry_delay_seconds": entry_delay_seconds,
                "resolved": False,
                "touched": pd.NA,
                "touch_time": pd.NaT,
                "touch_price": np.nan,
                "max_price": np.nan,
                "min_price": np.nan,
                "price_at_end": np.nan,
                "ticks_in_window": ticks_in_window,
                "max_favorable_excursion": np.nan,
                "unresolved_reason": "insufficient_window_ticks",
            })
            continue

        window_epochs_arr = tick_epochs[window_mask]
        max_price = float(window_prices.max())
        min_price = float(window_prices.min())
        price_at_end = float(window_prices[-1])

        if barrier_direction == "upper":
            touch_mask = window_prices >= barrier_level
            mfe = max_price - entry_price
        else:
            touch_mask = window_prices <= barrier_level
            mfe = entry_price - min_price

        touched = bool(touch_mask.any())
        touch_time = pd.NaT
        touch_price = np.nan
        if touched:
            first_touch_idx = touch_mask.nonzero()[0][0]
            touch_epoch = window_epochs_arr[first_touch_idx]
            touch_time = pd.Timestamp(touch_epoch, unit="s", tz="UTC")
            touch_price = float(window_prices[first_touch_idx])

        results.append({
            "entry_time": entry_time,
            "entry_price": entry_price,
            "barrier_level": barrier_level,
            "barrier_direction": barrier_direction,
            "barrier_distance": barrier_distance,
            "barrier_unit": "relative_price_points" if barrier_type == "relative" else "absolute_price",
            "duration_seconds": duration_seconds,
            "entry_delay_seconds": entry_delay_seconds,
            "resolved": True,
            "touched": touched,
            "touch_time": touch_time,
            "touch_price": touch_price,
            "max_price": max_price,
            "min_price": min_price,
            "price_at_end": price_at_end,
            "ticks_in_window": ticks_in_window,
            "max_favorable_excursion": mfe,
            "unresolved_reason": None,
        })

    return pd.DataFrame(results)


def _unresolved_row(entry_time, barrier_direction: str, reason: str) -> dict:
    return {
        "entry_time": entry_time,
        "entry_price": np.nan,
        "barrier_level": np.nan,
        "barrier_direction": barrier_direction,
        "barrier_distance": np.nan,
        "barrier_unit": "relative_price_points",
        "duration_seconds": np.nan,
        "entry_delay_seconds": np.nan,
        "resolved": False,
        "touched": pd.NA,
        "touch_time": pd.NaT,
        "touch_price": np.nan,
        "max_price": np.nan,
        "min_price": np.nan,
        "price_at_end": np.nan,
        "ticks_in_window": 0,
        "max_favorable_excursion": np.nan,
        "unresolved_reason": reason,
    }


def filter_resolved_labels(labels_df: pd.DataFrame) -> pd.DataFrame:
    """Keep only resolved labels with a defined touched outcome."""
    if labels_df.empty:
        return labels_df
    if "resolved" not in labels_df.columns:
        return labels_df
    out = labels_df[labels_df["resolved"] == True].copy()  # noqa: E712
    out = out[out["touched"].notna()]
    return out.reset_index(drop=True)


def compute_historical_touch_frequency(
    labels_df: pd.DataFrame,
    group_columns: list[str] = None,
) -> pd.DataFrame:
    """Compute historical touch frequency conditional on grouping columns."""
    if labels_df.empty:
        return pd.DataFrame()

    df = filter_resolved_labels(labels_df) if "resolved" in labels_df.columns else labels_df
    if group_columns is None:
        group_columns = ["barrier_direction"]

    grouped = df.groupby(group_columns).agg(
        total=("touched", "count"),
        touches=("touched", "sum"),
    )
    grouped["touch_rate"] = grouped["touches"] / grouped["total"]
    grouped["no_touch_rate"] = 1 - grouped["touch_rate"]
    return grouped.reset_index()
