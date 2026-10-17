"""
Touch outcome labeling — determines whether a barrier was touched
during a contract window using actual tick data.

IMPORTANT:
- Labels are determined by actual ticks, NOT candle closing prices.
- Upper and lower touches are separate events — both can happen in one window.
- A pre-entry touch is NOT a winning contract.
- Processing start is decision time plus the assumed entry delay.
- Relative barriers use the first tick strictly after processing start.
- Expiry is processing start plus duration; the anchor tick is excluded from hits.
- Incomplete futures and material gaps are unresolved (excluded from training).
"""

from __future__ import annotations

from decimal import Decimal

import numpy as np
import pandas as pd
import structlog

logger = structlog.get_logger(__name__)

# Default minimum ticks in outcome window; trainer uses a stricter value.
MIN_WINDOW_TICKS = 1
# Reject paths whose last observed tick is farther than this from expiry.
DEFAULT_MAX_END_SLACK_SECONDS = 5
# Reject windows containing an internal tick gap larger than this.
DEFAULT_MAX_GAP_SECONDS = 5

# Changing the anchor changes the prediction target, even with identical barriers.
TOUCH_LABEL_SEMANTICS = "next_tick_after_processing_start_v1"


def label_touch_outcomes(
    ticks_df: pd.DataFrame,
    entry_times: pd.Series,
    duration_seconds: int,
    barrier_distance: float,
    barrier_direction: str = "upper",
    barrier_type: str = "relative",
    entry_delay_seconds: int = 0,
    min_window_ticks: int = MIN_WINDOW_TICKS,
    max_gap_seconds: int = DEFAULT_MAX_GAP_SECONDS,
    max_end_slack_seconds: int = DEFAULT_MAX_END_SLACK_SECONDS,
) -> pd.DataFrame:
    """
    For each decision time, simulate server processing after `entry_delay_seconds`.
    The next tick supplies the entry spot. Expiry remains duration_seconds after
    processing, so waiting for that tick does not extend the contract.

    Barrier distance for relative contracts is in **price points**
    (same units as Deriv relative barrier input, e.g. 0.09 or 0.2),
    not percent.

    Returns columns including:
      resolved (bool) — False when the path is incomplete or gapped
      touched (bool|NA) — only meaningful when resolved=True
      decision_epoch / processing_start_epoch / actual_entry_epoch / expiry_epoch
      entry_price / barrier_level / label_semantics
    """
    if ticks_df.empty or entry_times.empty:
        return pd.DataFrame()

    ticks_sorted = ticks_df.sort_values("epoch").reset_index(drop=True)
    tick_prices = ticks_sorted["quote"].to_numpy(dtype=float)
    tick_epochs = ticks_sorted["epoch"].to_numpy(dtype=np.int64)
    delay = max(0, int(entry_delay_seconds))
    duration = int(duration_seconds)
    max_gap = max(1, int(max_gap_seconds))
    end_slack = max(0, int(max_end_slack_seconds))

    results = []
    for entry_time in entry_times:
        decision_epoch = (
            int(entry_time.timestamp()) if hasattr(entry_time, "timestamp") else int(entry_time)
        )
        processing_start_epoch = decision_epoch + delay
        window_end_epoch = processing_start_epoch + duration
        timing = {
            "decision_epoch": decision_epoch,
            "processing_start_epoch": processing_start_epoch,
            "expiry_epoch": window_end_epoch,
            "duration_seconds": duration,
            "entry_delay_seconds": delay,
        }
        if duration <= 0:
            results.append(
                _unresolved_row(
                    entry_time,
                    barrier_direction,
                    reason="invalid_duration",
                    **timing,
                )
            )
            continue

        # "Now" contracts use the next tick after server processing, not the
        # decision tick or a tick whose timestamp equals processing start.
        # https://smarttrader.deriv.me/en/trading (Entry spot / Contract period)
        entry_idx = int(np.searchsorted(tick_epochs, processing_start_epoch, side="right"))
        if entry_idx >= len(tick_epochs):
            results.append(
                _unresolved_row(
                    entry_time,
                    barrier_direction,
                    reason="no_entry_price",
                    **timing,
                )
            )
            continue
        actual_entry_epoch = int(tick_epochs[entry_idx])
        if actual_entry_epoch >= window_end_epoch:
            results.append(
                _unresolved_row(
                    entry_time,
                    barrier_direction,
                    reason="no_post_entry_window",
                    actual_entry_epoch=actual_entry_epoch,
                    **timing,
                )
            )
            continue
        if actual_entry_epoch - processing_start_epoch > max_gap:
            results.append(
                _unresolved_row(
                    entry_time,
                    barrier_direction,
                    reason="stale_entry",
                    actual_entry_epoch=actual_entry_epoch,
                    **timing,
                )
            )
            continue

        entry_price = float(tick_prices[entry_idx])
        if barrier_type == "relative":
            # Prices/offsets are decimal broker values. Binary addition can put
            # 100.2 + 0.01 one ULP above the actual tick 100.21 and miss equality.
            offset = Decimal(str(barrier_distance))
            if barrier_direction == "upper":
                barrier_level = float(Decimal(str(entry_price)) + offset)
            else:
                barrier_level = float(Decimal(str(entry_price)) - offset)
        else:
            barrier_level = float(barrier_distance)

        # Need a tick at or after expiry so the no-touch path is complete.
        end_idx = int(np.searchsorted(tick_epochs, window_end_epoch, side="left"))
        if end_idx >= len(tick_epochs):
            results.append(
                _unresolved_row(
                    entry_time,
                    barrier_direction,
                    reason="incomplete_future",
                    actual_entry_epoch=actual_entry_epoch,
                    entry_price=entry_price,
                    barrier_level=barrier_level,
                    barrier_distance=barrier_distance,
                    **timing,
                )
            )
            continue

        # Gaps from accepted entry through first tick at/after expiry.
        span_epochs = tick_epochs[entry_idx : end_idx + 1]
        if len(span_epochs) >= 2 and int(np.diff(span_epochs).max()) > max_gap:
            results.append(
                _unresolved_row(
                    entry_time,
                    barrier_direction,
                    reason="material_gap",
                    actual_entry_epoch=actual_entry_epoch,
                    entry_price=entry_price,
                    barrier_level=barrier_level,
                    barrier_distance=barrier_distance,
                    **timing,
                )
            )
            continue

        window_mask = (tick_epochs > actual_entry_epoch) & (tick_epochs <= window_end_epoch)
        ticks_in_window = int(window_mask.sum())
        if ticks_in_window < int(min_window_ticks):
            results.append(
                _unresolved_row(
                    entry_time,
                    barrier_direction,
                    reason="insufficient_window_ticks",
                    actual_entry_epoch=actual_entry_epoch,
                    entry_price=entry_price,
                    barrier_level=barrier_level,
                    barrier_distance=barrier_distance,
                    ticks_in_window=ticks_in_window,
                    **timing,
                )
            )
            continue

        window_prices = tick_prices[window_mask]
        window_epochs_arr = tick_epochs[window_mask]
        last_window_epoch = int(window_epochs_arr[-1])
        if window_end_epoch - last_window_epoch > end_slack and int(tick_epochs[end_idx]) > window_end_epoch:
            # Path jumped over expiry without a near-expiry observation.
            if int(tick_epochs[end_idx]) - last_window_epoch > max_gap:
                results.append(
                    _unresolved_row(
                        entry_time,
                        barrier_direction,
                        reason="incomplete_future",
                        actual_entry_epoch=actual_entry_epoch,
                        entry_price=entry_price,
                        barrier_level=barrier_level,
                        barrier_distance=barrier_distance,
                        ticks_in_window=ticks_in_window,
                        **timing,
                    )
                )
                continue

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
            touch_time = pd.Timestamp(int(touch_epoch), unit="s", tz="UTC")
            touch_price = float(window_prices[first_touch_idx])

        results.append(
            {
                "entry_time": entry_time,
                "decision_epoch": decision_epoch,
                "processing_start_epoch": processing_start_epoch,
                "actual_entry_epoch": actual_entry_epoch,
                "expiry_epoch": window_end_epoch,
                "label_semantics": TOUCH_LABEL_SEMANTICS,
                "entry_price": entry_price,
                "barrier_level": barrier_level,
                "barrier_direction": barrier_direction,
                "barrier_distance": barrier_distance,
                "barrier_unit": "relative_price_points" if barrier_type == "relative" else "absolute_price",
                "duration_seconds": duration,
                "entry_delay_seconds": delay,
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
            }
        )

    return pd.DataFrame(results)


def _unresolved_row(
    entry_time,
    barrier_direction: str,
    reason: str,
    *,
    decision_epoch: int | None = None,
    processing_start_epoch: int | None = None,
    actual_entry_epoch: int | None = None,
    expiry_epoch: int | None = None,
    entry_price: float = np.nan,
    barrier_level: float = np.nan,
    barrier_distance: float = np.nan,
    duration_seconds: float = np.nan,
    entry_delay_seconds: float = np.nan,
    ticks_in_window: int = 0,
) -> dict:
    return {
        "entry_time": entry_time,
        "decision_epoch": decision_epoch,
        "processing_start_epoch": processing_start_epoch,
        "actual_entry_epoch": actual_entry_epoch,
        "expiry_epoch": expiry_epoch,
        "label_semantics": TOUCH_LABEL_SEMANTICS,
        "entry_price": entry_price,
        "barrier_level": barrier_level,
        "barrier_direction": barrier_direction,
        "barrier_distance": barrier_distance,
        "barrier_unit": "relative_price_points",
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
