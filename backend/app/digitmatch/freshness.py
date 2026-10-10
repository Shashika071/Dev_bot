"""Freshness policy: skip rather than submit a decision overtaken by a new tick."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class Freshness:
    ok: bool
    reason: str


def tick_is_fresh(*, received_at: datetime | None, now: datetime, max_age_seconds: float) -> Freshness:
    if received_at is None:
        return Freshness(False, "no_tick")
    if received_at.tzinfo is None:
        received_at = received_at.replace(tzinfo=timezone.utc)
    age = (now - received_at).total_seconds()
    if age > max_age_seconds:
        return Freshness(False, "stale_tick")
    if age < 0:
        return Freshness(False, "tick_clock_ahead")
    return Freshness(True, "fresh_tick")


def proposal_is_fresh(
    *,
    proposal_epoch: int | None,
    latest_tick_epoch: int | None,
    proposal_age_seconds: float | None,
    max_proposal_age_seconds: float,
) -> Freshness:
    if proposal_epoch is None or latest_tick_epoch is None:
        return Freshness(False, "proposal_missing_spot")
    if proposal_epoch < latest_tick_epoch:
        return Freshness(False, "proposal_older_than_tick")
    if proposal_age_seconds is None or proposal_age_seconds > max_proposal_age_seconds:
        return Freshness(False, "stale_proposal")
    return Freshness(True, "fresh_proposal")
