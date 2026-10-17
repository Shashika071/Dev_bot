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

    # Research / force / exploratory paths must never buy.
    if signal_data.get("research_only") or signal_data.get("force_model_candles"):
        return {
            "ok": False,
            "skipped": True,
            "reason": "Research or force-model signals cannot place orders",
            "execution": status,
        }
    if signal_data.get("cross_barrier"):
        model_b = signal_data.get("score_barrier_distance")
        trade_b = signal_data.get("barrier_distance")
        try:
            if model_b is None or trade_b is None or abs(float(model_b) - float(trade_b)) > 1e-9:
                return {
                    "ok": False,
                    "skipped": True,
                    "reason": "Cross-barrier model target differs from trade barrier; execution blocked",
                    "execution": status,
                }
        except (TypeError, ValueError):
            return {
                "ok": False,
                "skipped": True,
                "reason": "Cross-barrier contract identity is incomplete; execution blocked",
                "execution": status,
            }
    if signal_data.get("is_validated") is False:
        return {
            "ok": False,
            "skipped": True,
            "reason": "Signal is not validated for execution",
            "execution": status,
        }

    from app.deriv.auto_trade import maybe_auto_trade_after_signal

    result = await maybe_auto_trade_after_signal(session, signal_data)
    result["execution"] = status
    return result
