"""
Daily signal cap and cooldown management.
Persists across restarts using the database.

Rules:
- Max 3 signals per day in Asia/Colombo timezone
- Configurable cooldown between signals (default: no overlapping = 540s)
- Counter resets at midnight Asia/Colombo
- Select opportunities online using fixed rules — never use future information
"""

from datetime import datetime, timedelta
from typing import Optional
import pytz
import structlog
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.signal import Signal
from app.config import settings

logger = structlog.get_logger(__name__)


class DailyCapManager:
    """
    Manages daily signal cap and cooldown, persisted via database.
    """

    def __init__(
        self,
        max_per_day: int = None,
        cooldown_seconds: int = None,
        timezone_name: str = None,
    ):
        self.max_per_day = max_per_day or settings.max_signals_per_day
        self.cooldown_seconds = cooldown_seconds or settings.signal_cooldown_seconds
        self.tz = pytz.timezone(timezone_name or settings.app_timezone)

    def get_today_date(self) -> str:
        """Get today's date string in the configured timezone."""
        now = datetime.now(self.tz)
        return now.strftime("%Y-%m-%d")

    async def get_signals_today(self, session: AsyncSession, symbol: str = None) -> int:
        """Count signals issued today (from DB for restart safety)."""
        today = self.get_today_date()
        query = select(func.count(Signal.id)).where(Signal.day_date == today)
        if symbol:
            query = query.where(Signal.symbol == symbol)
        result = await session.execute(query)
        return result.scalar() or 0

    async def can_issue_signal(
        self,
        session: AsyncSession,
        symbol: str,
    ) -> tuple[bool, str]:
        """
        Check if a new signal can be issued.
        Returns (allowed, reason).
        """
        today = self.get_today_date()

        # Check daily cap
        count_today = await self.get_signals_today(session, symbol)
        if count_today >= self.max_per_day:
            return False, f"Daily cap reached: {count_today}/{self.max_per_day} signals today"

        # Cooldown from latest signal created_at (any status) for this symbol
        last_signal = await session.execute(
            select(Signal)
            .where(Signal.symbol == symbol)
            .order_by(Signal.created_at.desc())
            .limit(1)
        )
        last = last_signal.scalar_one_or_none()

        if last and last.created_at:
            now = datetime.now(pytz.UTC)
            created = last.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=pytz.UTC)
            elapsed = (now - created).total_seconds()
            if elapsed < self.cooldown_seconds:
                remaining = int(self.cooldown_seconds - elapsed)
                return False, f"Cooldown active: {remaining}s remaining"

        return True, f"Signal allowed ({count_today + 1}/{self.max_per_day} today)"

    async def get_next_sequence(self, session: AsyncSession) -> int:
        """Get the next signal sequence number for today."""
        today = self.get_today_date()
        result = await session.execute(
            select(func.max(Signal.day_sequence)).where(Signal.day_date == today)
        )
        max_seq = result.scalar()
        return (max_seq or 0) + 1

    async def get_status(self, session: AsyncSession, symbol: str = None) -> dict:
        """Get current daily cap status."""
        today = self.get_today_date()
        count = await self.get_signals_today(session, symbol)
        can_issue, reason = await self.can_issue_signal(session, symbol or "R_100")

        cooldown_active = (not can_issue) and ("Cooldown" in reason)
        return {
            "date": today,
            "timezone": str(self.tz),
            "signals_today": count,
            "max_per_day": self.max_per_day,
            "max_signals": self.max_per_day,  # frontend alias
            "remaining": max(0, self.max_per_day - count),
            "can_issue": can_issue,
            "cooldown_active": cooldown_active,
            "reason": reason,
            "cooldown_seconds": self.cooldown_seconds,
        }
