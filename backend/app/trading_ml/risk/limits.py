"""Fixed-stake limits. Losses never increase the next stake."""
from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.trading_ml.config import DEFAULT_LIMITS, TIMEZONE


def next_stake(_previous_profit: float | None = None) -> float:
    return 1.0


def colombo_day(epoch: int) -> str:
    return datetime.fromtimestamp(int(epoch), ZoneInfo(TIMEZONE)).date().isoformat()


def load_limits(path: Path | None) -> tuple[dict, list[str]]:
    limits = dict(DEFAULT_LIMITS)
    if path is None or not path.exists():
        return limits, []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        limits["emergency_stop"] = True
        return limits, ["Risk settings could not be read. New signals are blocked."]
    if not isinstance(payload, dict):
        limits["emergency_stop"] = True
        return limits, ["Risk settings must be a JSON object. New signals are blocked."]
    for key, value in payload.items():
        if key in limits:
            limits[key] = value
    blockers = []
    if limits["emergency_stop"] is True:
        blockers.append("Emergency stop is on.")
    elif type(limits["emergency_stop"]) is not bool:
        limits["emergency_stop"] = True
        blockers.append("Emergency stop setting is invalid. New signals are blocked.")
    return limits, blockers


def risk_blockers(*, limits: dict, paper: dict, trained_at: int | None, now: int,
                  test_selected: int, extra: list[str] | None = None) -> list[str]:
    blockers = list(extra or [])
    if limits.get("emergency_stop") is True and "Emergency stop is on." not in blockers:
        blockers.append("Emergency stop is on.")
    if trained_at is None:
        blockers.append("The research model has no training timestamp.")
    elif now - int(trained_at) > int(limits["max_model_age_seconds"]):
        blockers.append("The research model is older than the allowed age.")
    if int(paper.get("daily_signals") or 0) >= int(limits["daily_signal_limit"]):
        blockers.append("The daily paper-signal limit has been reached.")
    if int(paper.get("consecutive_losses") or 0) >= int(limits["max_consecutive_losses"]):
        blockers.append("Paper trading reached the consecutive-loss limit.")
    drawdown = paper.get("max_drawdown")
    if drawdown is not None and float(drawdown) >= float(limits["max_drawdown"]):
        blockers.append("Paper trading reached the drawdown limit.")
    if int(test_selected) < int(limits["min_historical_signals"]):
        blockers.append("The untouched test does not contain the required number of signals.")
    resolved = int(paper.get("resolved") or 0)
    lower = paper.get("wilson_lower")
    breakeven = paper.get("mean_break_even")
    if resolved >= 20 and lower is not None and breakeven is not None and float(lower) < float(breakeven):
        blockers.append("Resolved paper trades deteriorated below their payout break-even.")
    if any(not isinstance(item, str) for item in blockers):
        return ["Risk checks returned an invalid result."]
    return blockers


def ev_floor(limits: dict) -> float:
    floor = float(limits["min_ev"]) + float(limits["safety_margin"])
    if not math.isfinite(floor) or floor < 0:
        raise ValueError("Expected-value floor must be finite and non-negative")
    return floor
