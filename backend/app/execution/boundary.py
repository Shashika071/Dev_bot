"""The only analysis-facing entry point that may reach order code."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.execution.gates import permission_status


async def maybe_execute_signal(
    session: AsyncSession,
    signal_data: dict[str, Any],
) -> dict[str, Any]:
    """Place an optional order only after both execution gates pass."""
    status = permission_status()
    if not status["allowed"]:
        return {"ok": False, "skipped": True, "reason": status["reason"], "execution": status}
    from app.deriv.auto_trade import maybe_auto_trade_after_signal

    result = await maybe_auto_trade_after_signal(session, signal_data)
    result["execution"] = status
    return result
