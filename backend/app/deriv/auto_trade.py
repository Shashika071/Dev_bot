"""
Optional buy after UI Analyze / Force signal.

Hard safety: disabled by default; refuses when stake ≤ 0, token missing, or authorize fails.
Never crashes the caller — returns {ok, error, ...}.
"""

from __future__ import annotations

from typing import Any, Optional

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.deriv.trade_session import trade_account_session
from app.models.signal import Signal
from app.signal_engine.settings_lookup import get_latest_confirmed_settings
from app.trade_prefs import get_trade_token, load_trade_prefs

logger = structlog.get_logger(__name__)


async def _resolve_full_balance_stake() -> float:
    """
    Stake ≈ live account balance (floor to 2 decimals).
    Leaves a 0.01 cushion so Deriv can accept the buy when balance == stake.
    """
    bal = await trade_account_session.get_live_balance()
    if bal is None:
        try:
            from app.deriv.trade_client import DerivTradeClient
            from app.trade_prefs import resolve_deriv_app_id

            token = get_trade_token()
            if not token:
                return 0.0
            client = DerivTradeClient(token, app_id=resolve_deriv_app_id() or None)
            try:
                summary = await client.fetch_account_summary()
                bal = float(summary.get("balance") or 0)
            finally:
                await client.close()
        except Exception as e:
            logger.warning("full_balance_oneshot_failed", error=str(e))
            return 0.0

    # Floor to cents; keep 0.01 so proposal/buy isn't rejected for exact balance
    raw = max(0.0, float(bal) - 0.01)
    stake = float(int(raw * 100) / 100.0)
    return max(0.0, min(10000.0, stake))


async def maybe_auto_trade_after_signal(
    session: AsyncSession,
    signal_data: dict[str, Any],
) -> dict[str, Any]:
    """
    If auto-trade is enabled and token is set, place a One-Touch buy for the signal.
    Persists a short note on the signal row when possible.
    """
    prefs = load_trade_prefs()
    if not prefs.get("auto_trade_enabled"):
        return {"ok": False, "skipped": True, "reason": "auto_trade_disabled"}

    token = get_trade_token()
    if not token:
        return {"ok": False, "error": "Deriv API token not configured"}

    symbol = signal_data.get("symbol")
    direction = signal_data.get("direction")
    barrier = signal_data.get("barrier_input")
    if not symbol or not direction or not barrier:
        return {"ok": False, "error": "Signal missing symbol/direction/barrier"}

    conf = await get_latest_confirmed_settings(session)
    if not conf:
        return {"ok": False, "error": "No confirmed contract settings"}

    duration = int(conf.duration_value)
    duration_unit = str(conf.duration_unit or "m")
    currency = str(prefs.get("trade_currency") or conf.currency or "USD")

    use_full = bool(prefs.get("trade_stake_use_full_balance"))
    stake = float(prefs.get("trade_stake") or 0)
    if use_full:
        stake = await _resolve_full_balance_stake()
        if stake < 0.35:
            return {
                "ok": False,
                "error": f"Full-balance stake too small ({stake}). Need balance ≥ 0.35",
            }
        logger.info("auto_trade_full_balance_stake", stake=stake)
    elif stake <= 0:
        return {"ok": False, "error": "trade_stake must be > 0"}

    # Shared live trade WS — avoids a new PAT/OTP per buy
    result = await trade_account_session.place_one_touch(
        symbol=str(symbol),
        direction=str(direction),
        barrier=str(barrier),
        duration=duration,
        duration_unit=duration_unit,
        stake=stake,
        currency=currency,
    )

    # Persist contract id on signal notes
    signal_id = signal_data.get("signal_id")
    if signal_id:
        try:
            from sqlalchemy import select

            row = (
                await session.execute(select(Signal).where(Signal.signal_id == signal_id))
            ).scalar_one_or_none()
            if row is not None:
                note_bits = []
                if row.entry_notes:
                    note_bits.append(row.entry_notes)
                if result.get("ok"):
                    note_bits.append(
                        f"AUTO_TRADE ok contract_id={result.get('contract_id')} "
                        f"buy_price={result.get('buy_price')} stake={stake} "
                        f"loginid={result.get('loginid')}"
                    )
                else:
                    note_bits.append(
                        f"AUTO_TRADE failed: {result.get('error') or 'unknown'}"
                    )
                row.entry_notes = " | ".join(note_bits)
                await session.commit()
        except Exception as e:
            logger.warning("auto_trade_note_persist_failed", error=str(e))

    if result.get("ok"):
        logger.info(
            "auto_trade_buy_ok",
            signal_id=signal_id,
            contract_id=result.get("contract_id"),
            stake=stake,
        )
    else:
        logger.error(
            "auto_trade_buy_failed",
            signal_id=signal_id,
            error=result.get("error"),
        )

    return {
        "ok": bool(result.get("ok")),
        "contract_id": result.get("contract_id"),
        "buy_price": result.get("buy_price"),
        "longcode": result.get("longcode"),
        "loginid": result.get("loginid"),
        "stake": stake,
        "error": result.get("error"),
        "skipped": False,
    }
