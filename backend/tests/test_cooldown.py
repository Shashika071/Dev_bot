"""Cooldown uses latest signal regardless of lifecycle status."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.signal_engine.daily_cap import DailyCapManager


@pytest.mark.asyncio
async def test_cooldown_blocks_regardless_of_status():
    mgr = DailyCapManager(max_per_day=3, cooldown_seconds=540)
    recent = MagicMock()
    recent.created_at = datetime.now(timezone.utc) - timedelta(seconds=30)
    recent.symbol = "R_100"

    session = AsyncMock()
    # get_signals_today count
    count_result = MagicMock()
    count_result.scalar.return_value = 1
    # last signal
    last_result = MagicMock()
    last_result.scalar_one_or_none.return_value = recent

    session.execute = AsyncMock(side_effect=[count_result, last_result])

    ok, reason = await mgr.can_issue_signal(session, "R_100")
    assert ok is False
    assert "Cooldown" in reason


@pytest.mark.asyncio
async def test_cooldown_allows_after_elapsed():
    mgr = DailyCapManager(max_per_day=3, cooldown_seconds=60)
    old = MagicMock()
    old.created_at = datetime.now(timezone.utc) - timedelta(seconds=120)

    session = AsyncMock()
    count_result = MagicMock()
    count_result.scalar.return_value = 1
    last_result = MagicMock()
    last_result.scalar_one_or_none.return_value = old
    session.execute = AsyncMock(side_effect=[count_result, last_result])

    ok, reason = await mgr.can_issue_signal(session, "R_100")
    assert ok is True
