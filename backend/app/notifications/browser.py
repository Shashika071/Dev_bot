"""
Browser push notification support via WebSocket.

The API process owns the browser WebSocket clients. The worker process publishes
status/signals over HTTP into this module so the UI can receive them.
"""

import json
from typing import Any
from datetime import datetime, timezone

import structlog

logger = structlog.get_logger(__name__)

# Price updates, alerts, and connection status each have their own listeners.
_channels: dict[str, set] = {
    "status": set(),
    "notifications": set(),
    "market": set(),
}

# Latest worker heartbeat (API process memory)
_latest_status: dict[str, Any] = {
    "connected": False,
    "deriv_connected": False,
    "ticks_collected": 0,
    "quotes_collected": 0,
    "symbol": None,
    "updated_at": None,
}


def register_client(ws, channel: str = "notifications"):
    """Register a WebSocket client for one kind of push."""
    _channels.setdefault(channel, set()).add(ws)
    logger.info(
        "browser_notification_client_registered",
        channel=channel,
        total=len(_channels[channel]),
    )


def unregister_client(ws):
    """Unregister a WebSocket client from every channel."""
    for channel, bucket in _channels.items():
        if ws in bucket:
            bucket.discard(ws)
            logger.info(
                "browser_notification_client_unregistered",
                channel=channel,
                total=len(bucket),
            )


def normalize_status(status_data: dict) -> dict[str, Any]:
    """Normalize worker/API status payloads for the frontend."""
    connected = bool(status_data.get("deriv_connected", status_data.get("connected", False)))
    return {
        "connected": connected,
        "deriv_connected": connected,
        "ticks_collected": int(status_data.get("ticks_collected") or 0),
        "quotes_collected": int(status_data.get("quotes_collected") or 0),
        "symbol": status_data.get("symbol"),
        "market_state": status_data.get("market_state"),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def get_latest_status() -> dict[str, Any]:
    """Return the most recent worker status snapshot."""
    return dict(_latest_status)


async def send_notification(event_type: str, data: dict, *, channels: tuple[str, ...]):
    """Send one event to the listeners registered for these channels."""
    message = json.dumps({
        "type": event_type,
        "data": data,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    disconnected: list[tuple[str, Any]] = []
    for channel in channels:
        for ws in list(_channels.get(channel) or ()):
            try:
                await ws.send_text(message)
            except Exception:
                disconnected.append((channel, ws))
    for channel, ws in disconnected:
        _channels.get(channel, set()).discard(ws)


async def send_signal_notification(signal_data: dict):
    """Send a signal alert to the alert socket and the market chart."""
    await send_notification("signal_alert", signal_data, channels=("notifications", "market"))


async def send_status_update(status_data: dict):
    """Store + broadcast a status update to the connection-status socket only."""
    global _latest_status
    normalized = normalize_status(status_data)
    _latest_status = normalized
    await send_notification("status_update", normalized, channels=("status",))
