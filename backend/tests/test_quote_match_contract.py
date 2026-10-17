"""Wrong-contract quotes must never count as matched coverage."""

import numpy as np
import pandas as pd

from app.ml.quote_match import match_quotes_to_entries


def test_quote_match_rejects_wrong_barrier_and_duration():
    entries = np.array([1_700_000_100], dtype=np.int64)
    quotes = pd.DataFrame(
        [
            {
                "quote_epoch": 1_700_000_050,
                "barrier_direction": "upper",
                "contract_type": "ONETOUCH",
                "barrier_input": "+0.30",
                "duration_value": 540,
                "duration_unit": "s",
                "currency": "USD",
                "ask_price": 1.0,
                "payout": 1.05,
                "breakeven_prob": 1.0 / 1.05,
            },
            {
                "quote_epoch": 1_700_000_060,
                "barrier_direction": "upper",
                "contract_type": "ONETOUCH",
                "barrier_input": "+0.08",
                "duration_value": 300,
                "duration_unit": "s",
                "currency": "USD",
                "ask_price": 1.0,
                "payout": 1.10,
                "breakeven_prob": 1.0 / 1.10,
            },
        ]
    )
    wrong = match_quotes_to_entries(
        entries,
        quotes,
        direction="upper",
        barrier_distance=0.08,
        duration_seconds=540,
        contract_type="ONETOUCH",
    )
    assert wrong["match_rate"] == 0.0
    assert bool(wrong["matched"][0]) is False

    quotes.loc[1, "duration_value"] = 540
    ok = match_quotes_to_entries(
        entries,
        quotes,
        direction="upper",
        barrier_distance=0.08,
        duration_seconds=540,
        contract_type="ONETOUCH",
    )
    assert ok["match_rate"] == 1.0
    assert bool(ok["matched"][0]) is True
    assert abs(float(ok["quote_payouts"][0]) - 1.10) < 1e-9
