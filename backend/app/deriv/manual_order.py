"""Manual One-Touch orders. The user clicks Place; this does not turn auto-trade on."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.deriv.barriers import touch_duration, touch_order_fields
from app.execution.risk import load_loss_today
from app.signal_engine.settings_lookup import get_latest_confirmed_settings
from app.trade_prefs import get_trade_token, load_trade_prefs


async def place_manual_touch(
    session: AsyncSession,
    *,
    symbol: str,
    direction: str,
    barrier: str,
    stake: float,
    duration: int | None = None,
    duration_unit: str | None = None,
) -> dict[str, Any]:
    if not get_trade_token():
        return {"ok": False, "error": "No trade token configured. Add it in Configuration."}
    prefs = load_trade_prefs()
    limit = float(prefs.get("daily_loss_limit") or 0)
    if limit > 0 and load_loss_today() >= limit:
        return {"ok": False, "error": "Daily loss limit reached"}
    try:
        ticket = touch_order_fields(direction, barrier, stake)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    conf = await get_latest_confirmed_settings(session)
    if duration is None:
        if conf is None:
            return {"ok": False, "error": "Set the touch duration on the chart"}
        length = int(conf.duration_value)
        unit = str(conf.duration_unit or "m")
    else:
        try:
            length, unit = touch_duration(duration, duration_unit or "m")
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

    from app.deriv.trade_session import trade_account_session

    currency = str(prefs.get("trade_currency") or getattr(conf, "currency", None) or "USD")
    result = await trade_account_session.place_one_touch(
        symbol=str(symbol or (conf.symbol if conf is not None else "") or "R_100"),
        direction=ticket["direction"],
        barrier=ticket["barrier"],
        duration=length,
        duration_unit=unit,
        stake=float(ticket["stake"]),
        currency=currency,
    )
    result["contract_type"] = "ONETOUCH"
    result["direction"] = ticket["direction"]
    result["barrier"] = ticket["barrier"]
    result["stake"] = ticket["stake"]
    result["duration"] = length
    result["duration_unit"] = unit
    return result
