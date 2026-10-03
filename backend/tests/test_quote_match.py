"""Nearest-prior quote matching for economic backtests."""

import numpy as np
import pandas as pd

from app.ml.quote_match import match_quotes_to_entries


def test_match_quotes_finds_prior():
    quotes = pd.DataFrame(
        {
            "quote_epoch": [100, 200, 300],
            "barrier_direction": ["upper", "upper", "upper"],
            "ask_price": [9.5, 9.6, 9.7],
            "payout": [10.0, 10.0, 10.0],
            "breakeven_prob": [0.95, 0.96, 0.97],
        }
    )
    out = match_quotes_to_entries(
        np.array([250]), quotes, direction="upper", max_age_seconds=120
    )
    assert out["matched"][0]
    assert abs(out["breakeven_probs"][0] - 0.96) < 1e-9


def test_match_quotes_stale_unmatched():
    quotes = pd.DataFrame(
        {
            "quote_epoch": [100],
            "barrier_direction": ["upper"],
            "ask_price": [9.5],
            "payout": [10.0],
            "breakeven_prob": [0.95],
        }
    )
    out = match_quotes_to_entries(
        np.array([1000]), quotes, direction="upper", max_age_seconds=120
    )
    assert not out["matched"][0]
    assert abs(out["breakeven_probs"][0] - 0.956) < 1e-9
