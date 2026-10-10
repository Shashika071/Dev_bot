"""Redact tokens and authorization material before anything is logged."""

from __future__ import annotations

from typing import Any

_SENSITIVE = (
    "authorize",
    "authorization",
    "token",
    "password",
    "secret",
    "api_token",
    "cookie",
    "set-cookie",
)


def is_sensitive_key(key: str) -> bool:
    lowered = key.lower().replace("-", "_")
    return any(part in lowered for part in _SENSITIVE)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if is_sensitive_key(str(key)):
                cleaned[key] = "***"
            else:
                cleaned[key] = redact(item)
        return cleaned
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str) and value.lower().startswith("pat_"):
        return "***"
    return value


def safe_message_summary(message: dict) -> dict:
    """Log only the request type. Never the authorize payload."""
    keys = [key for key in message.keys() if key != "req_id"]
    summary = {"keys": keys}
    if "buy" in message:
        summary["has_buy"] = True
    if "proposal" in message:
        summary["contract_type"] = message.get("contract_type")
        summary["duration"] = message.get("duration")
        summary["duration_unit"] = message.get("duration_unit")
        summary["symbol"] = message.get("symbol")
    return summary


def redact_processor(_logger, _method, event_dict):
    return redact(event_dict)
