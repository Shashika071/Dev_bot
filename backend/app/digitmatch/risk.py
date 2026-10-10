"""Risk snapshot. Limits persist because they are read back from stored trades and settings."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class RiskSnapshot:
    timezone_name: str
    stake: float
    cooldown_seconds: int
    max_trades_per_day: int
    daily_loss_limit: float
    daily_profit_stop: float
    max_open_contracts: int
    open_contracts: int
    trades_today: int
    pnl_today: float
    last_settled_at: datetime | None
    emergency_stop: bool
    paused: bool
    uncertain: bool
    broker_min_stake: float | None = None


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reason: str


def trading_day_bounds(now: datetime, timezone_name: str) -> tuple[datetime, datetime]:
    tz = ZoneInfo(timezone_name)
    local = now.astimezone(tz)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def assess(snapshot: RiskSnapshot, now: datetime, next_loss: float) -> RiskDecision:
    if snapshot.emergency_stop:
        return RiskDecision(False, "emergency_stop")
    if snapshot.paused:
        return RiskDecision(False, "paused")
    if snapshot.uncertain:
        return RiskDecision(False, "uncertain_purchase")
    if snapshot.max_open_contracts < 1:
        return RiskDecision(False, "contracts_disabled")
    if snapshot.open_contracts >= snapshot.max_open_contracts:
        return RiskDecision(False, "open_contract")
    if snapshot.trades_today >= snapshot.max_trades_per_day:
        return RiskDecision(False, "daily_trade_limit")
    if snapshot.pnl_today >= snapshot.daily_profit_stop:
        return RiskDecision(False, "daily_profit_stop")
    if snapshot.pnl_today - abs(next_loss) <= -snapshot.daily_loss_limit:
        return RiskDecision(False, "daily_loss_limit")
    if snapshot.broker_min_stake is not None and snapshot.stake + 1e-9 < snapshot.broker_min_stake:
        return RiskDecision(False, "stake_below_broker_minimum")
    if snapshot.last_settled_at is not None:
        elapsed = (now - snapshot.last_settled_at).total_seconds()
        if elapsed < snapshot.cooldown_seconds:
            return RiskDecision(False, "cooldown")
    if next_loss < 0:
        return RiskDecision(False, "invalid_loss")
    return RiskDecision(True, "within_limits")
