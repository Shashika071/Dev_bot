"""Replay frozen ticks through the same snapshot, bundle, and trend policy."""

from __future__ import annotations

from typing import Any, Optional

import numpy as np
import pandas as pd

from app.features.snapshot import HISTORY_TICKS, build_decision_snapshot
from app.ml.quote_match import match_quotes_to_entries
from app.signal_engine.market_state import (
    MarketStateTracker,
    direction_blocked_by_trend,
    evaluate_market_state,
)


class TouchReplayRunner:
    def __init__(self, *, seq_len: int = 64, duration_seconds: int = 540, entry_delay_seconds: int = 5):
        self.seq_len = seq_len
        self.duration_seconds = duration_seconds
        self.entry_delay_seconds = entry_delay_seconds

    def run(
        self,
        ticks_df: pd.DataFrame,
        *,
        entry_epochs: list[int],
        barrier_distance: float,
        direction: str,
        bundle=None,
        quotes_df: pd.DataFrame | None = None,
        candles_by_epoch: dict | None = None,
        margin: float = 0.02,
    ) -> dict[str, Any]:
        tracker = MarketStateTracker(path=None)
        decisions = []
        epochs = np.asarray(entry_epochs, dtype=np.int64)
        quotes = match_quotes_to_entries(
            epochs,
            quotes_df if quotes_df is not None else pd.DataFrame(),
            direction=direction,
        )
        for index, epoch in enumerate(epochs.tolist()):
            snap = build_decision_snapshot(
                ticks_df,
                int(epoch),
                barrier_distance=barrier_distance,
                barrier_direction=direction,
                duration_seconds=self.duration_seconds,
                seq_len=self.seq_len,
                history=HISTORY_TICKS,
            )
            candles = (candles_by_epoch or {}).get(int(epoch))
            market = evaluate_market_state(
                official_candles=candles,
                features_df=snap.features if snap.ready else None,
                tracker=tracker,
                persist=True,
            )
            blocked, block_reason = direction_blocked_by_trend(direction, market)
            probability = None
            if bundle is not None and snap.ready and snap.sequence is not None:
                pred = bundle.predict_calibrated(
                    snap.features.iloc[[-1]],
                    np.expand_dims(snap.sequence, axis=0),
                )
                probability = float(pred["calibrated_probability"])
            breakeven = float(quotes["breakeven_probs"][index]) if quotes["matched"][index] else None
            would_signal = bool(
                probability is not None
                and breakeven is not None
                and probability > breakeven + margin
                and not blocked
            )
            decisions.append(
                {
                    "entry_epoch": int(epoch),
                    "ready": snap.ready,
                    "calibrated_probability": probability,
                    "quote_matched": bool(quotes["matched"][index]),
                    "breakeven_probability": breakeven,
                    "market_state": market.as_dict(),
                    "trend_blocked": blocked,
                    "trend_reason": block_reason,
                    "would_signal": would_signal,
                    "reject_reason": (
                        "trend_blocked" if blocked else None
                        if would_signal or probability is None
                        else "below_breakeven_or_unmatched_quote"
                    ),
                }
            )
        return {
            "decisions": decisions,
            "n": len(decisions),
            "would_signal": int(sum(1 for row in decisions if row["would_signal"])),
        }
