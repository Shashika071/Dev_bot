"""Dashboard state. The labels match the states the UI is required to show."""

from __future__ import annotations


def resolve_ui_state(snapshot: dict) -> str:
    if snapshot.get("uncertain_block"):
        return "uncertain_purchase"
    if snapshot.get("emergency_stop"):
        return "emergency_stop"
    if snapshot.get("auth_status") == "real_rejected":
        return "real_account_rejected"
    if not snapshot.get("credentials_configured"):
        return "credentials_missing"
    if (
        snapshot.get("auth_status") in {None, "unconfigured"}
        or snapshot.get("connection_status") != "online"
        or not snapshot.get("demo_verified")
    ):
        return "disconnected"
    if snapshot.get("instrument_error") or snapshot.get("contract_error"):
        return "contract_unavailable"
    if snapshot.get("stale"):
        return "stale_data"
    if not snapshot.get("features_ready"):
        return "warming_up"
    if snapshot.get("paused"):
        return "paused"
    if snapshot.get("risk_block") in {"daily_trade_limit", "daily_loss_limit", "daily_profit_stop"}:
        return "daily_limit"
    if snapshot.get("open_contract"):
        return "in_contract"
    if not snapshot.get("active_model"):
        return "no_model"
    if snapshot.get("mode") == "observe":
        return "observe"
    if snapshot.get("last_skip_reason") in {"no_qualifying_signal", "below_margin"}:
        return "no_qualifying_signal"
    return "ready"
