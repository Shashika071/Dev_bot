"""Frozen challenger promotion checks.

A model can be registered immediately, but it stays inactive until the
evidence gates passed and a shadow period of at least 14 days has elapsed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


SHADOW_PERIOD = timedelta(days=14)


def _parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def promotion_blockers(metadata: dict | None, *, now: datetime | None = None) -> list[str]:
    meta = metadata or {}
    blockers: list[str] = []
    if not bool(meta.get("has_demonstrated_edge")):
        blockers.append("no demonstrated edge")
    started = _parse_ts(meta.get("shadow_started_at"))
    if started is None:
        blockers.append("shadow period has not started")
    else:
        current = now or datetime.now(timezone.utc)
        if current - started < SHADOW_PERIOD:
            blockers.append("frozen shadow period is shorter than 14 days")
    if not bool(meta.get("lockbox_beats_baseline")):
        blockers.append("lockbox did not beat the constant baseline")
    return blockers
