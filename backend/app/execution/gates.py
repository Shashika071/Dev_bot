"""Two-gate permission check. This module must not place orders."""

from __future__ import annotations

from typing import Any

from app.config import settings
from app.execution.risk import load_loss_today
from app.trade_prefs import get_trade_token, load_trade_prefs


def server_execution_allowed(prefs: dict[str, Any] | None = None) -> bool:
    """
    Effective server gate from Setup → Auto-trade (UI).
    Replaces editing EXECUTION_ALLOWED in .env for day-to-day use.
    """
    prefs = prefs if prefs is not None else load_trade_prefs()
    return bool(prefs.get("server_execution_allowed"))


def permission_status(prefs: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return whether an order is allowed and the first failing reason."""
    prefs = prefs if prefs is not None else load_trade_prefs()
    limit = float(prefs.get("daily_loss_limit") or 0)
    loss = load_loss_today()
    server_ok = server_execution_allowed(prefs)
    status = {
        "server_allowed": server_ok,
        "env_execution_allowed": bool(settings.execution_allowed),
        "user_enabled": bool(prefs.get("auto_trade_enabled")),
        "user_confirmed": bool(prefs.get("execution_confirmed")),
        "daily_loss_limit": limit,
        "realized_loss_today": loss,
        "token_configured": bool(get_trade_token()),
        "allowed": False,
        "reason": "server_execution_disabled",
    }
    if not server_ok:
        return status
    if not prefs.get("auto_trade_enabled"):
        status["reason"] = "auto_trade_disabled"
        return status
    if not prefs.get("execution_confirmed"):
        status["reason"] = "execution_not_confirmed"
        return status
    if limit <= 0:
        status["reason"] = "daily_loss_limit_required"
        return status
    if loss >= limit:
        status["reason"] = "daily_loss_limit_reached"
        return status
    stake = float(prefs.get("trade_stake") or 0)
    if stake <= 0 and not prefs.get("trade_stake_use_full_balance"):
        status["reason"] = "stake_required"
        return status
    if not status["token_configured"]:
        status["reason"] = "token_missing"
        return status
    status["allowed"] = True
    status["reason"] = "allowed"
    return status
