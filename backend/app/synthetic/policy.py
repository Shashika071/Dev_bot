"""Volatility 100 product settings, independent of Forex sessions and news."""
from __future__ import annotations

import math

from app.forex.policy import local_day, supports_rise_fall

SYMBOL = "R_100"
DURATION_SECONDS = 300
TIMEFRAMES = {"5m": 300, "15m": 900, "1h": 3600}
MAX_ENTRY_AGE = 60


def default_config() -> dict:
    return {
        "symbols": [SYMBOL], "duration_seconds": DURATION_SECONDS,
        "entry_timeframe": "5m", "max_signals_per_day": 20,
        "cooldown_seconds": DURATION_SECONDS, "stake": 1.0,
        "timezone": "Asia/Colombo", "demo_auto_trade": False,
    }


def validate_config(updates: dict, previous: dict | None = None) -> dict:
    config = {**default_config(), **(previous or {}), **updates}
    if config["symbols"] != [SYMBOL]:
        raise ValueError("This strategy uses Volatility 100 (R_100) only")
    if config["duration_seconds"] != DURATION_SECONDS:
        raise ValueError("Rise/Fall expiry is fixed at five minutes")
    if isinstance(config["stake"], bool):
        raise ValueError("Invalid stake")
    stake = float(config["stake"])
    if not math.isfinite(stake) or not 0.35 <= stake <= 10:
        raise ValueError("Demo stake must be between 0.35 and 10 account currency units")
    cap = config["max_signals_per_day"]
    if type(cap) is not int or not 1 <= cap <= 100:
        raise ValueError("Daily signal cap must be an integer from 1 to 100")
    if type(config["demo_auto_trade"]) is not bool:
        raise ValueError("Demo execution must be explicitly enabled or disabled")
    return {
        **default_config(), "stake": round(stake, 2),
        "max_signals_per_day": cap, "demo_auto_trade": config["demo_auto_trade"],
    }
