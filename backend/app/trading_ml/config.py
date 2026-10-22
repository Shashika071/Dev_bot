"""Settings for the R_100 research signal engine."""
from __future__ import annotations

import math

SYMBOL = "R_100"
CONTRACT = "Rise/Fall"
DEFAULT_DURATION_SECONDS = 300
ALLOWED_DURATIONS = (60, 120, 180, 300, 600, 900, 1800, 3600)
STEP_SECONDS = 60
TIMEFRAMES = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "1d": 86400}
ENTRY_WINDOW_SECONDS = 20
QUOTE_MAX_AGE_SECONDS = 30
MIN_EVALUATION_BARS = 400
MIN_LABELED_ROWS = 120
MIN_SELECTED_FOR_APPROVAL = 100
MIN_SELECTED_FOR_PAPER = 20
TIMEZONE = "Asia/Colombo"

DEFAULT_LIMITS = {
    "daily_signal_limit": 10,
    "max_consecutive_losses": 4,
    "max_drawdown": 5.0,
    "max_model_age_seconds": 7 * 24 * 3600,
    "min_historical_signals": MIN_SELECTED_FOR_APPROVAL,
    "emergency_stop": False,
    "min_ev": 0.02,
    "safety_margin": 0.01,
}

LIMITATIONS = [
    "Only Deriv Volatility 100 (R_100) Rise/Fall is in this engine. It never places an order.",
    "A label uses the next candle open as the entry and the candle close at expiry as settlement. A live tick contract can settle differently.",
    "No payout is assumed. Expected value is empty until a net profit per unit stake is supplied.",
    "Training accuracy cannot approve a model. Approval uses the untouched chronological test.",
    "LSTM, GRU and temporal CNN are scored on earlier rows when PyTorch is installed. Their score is not the untouched test, and they do not vote.",
]


def validate_duration(value: int) -> int:
    if type(value) is not int or value not in ALLOWED_DURATIONS:
        allowed = ", ".join(str(item) for item in ALLOWED_DURATIONS)
        raise ValueError(f"Contract duration must be one of {allowed} seconds")
    return value


def validate_payout(value: float | None) -> float | None:
    """Net profit per 1 unit staked. 0.95 means a win pays 0.95 and a loss costs 1."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("Payout must be the net profit per unit stake, such as 0.95")
    try:
        payout = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Payout must be the net profit per unit stake, such as 0.95") from exc
    if not math.isfinite(payout) or not 0 < payout <= 3:
        raise ValueError(
            "Payout must be greater than 0 and at most 3 net profit per unit stake. "
            "Pass 0.95 for a 95% profit quote, not 80 or 95."
        )
    return payout


def validate_delay(value: int) -> int:
    if type(value) is not int or not 0 <= value <= STEP_SECONDS:
        raise ValueError("Entry delay must be an integer from 0 to 60 seconds")
    return value
