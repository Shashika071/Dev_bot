"""
Browser push notification support via WebSocket.

The API process owns the browser WebSocket clients. The worker process publishes
status/signals over HTTP into this module so the UI can receive them.
"""

import json
from typing import Any, Optional, Set
from datetime import datetime, timezone

import structlog

logger = structlog.get_logger(__name__)

# Connected WebSocket clients (API process only)
_ws_clients: Set = set()

# Latest worker heartbeat (API process memory)
_latest_status: dict[str, Any] = {
    "connected": False,
    "deriv_connected": False,
    "ticks_collected": 0,
    "quotes_collected": 0,
    "symbol": None,
    "updated_at": None,
}


def register_client(ws):
    """Register a WebSocket client for notifications."""
    _ws_clients.add(ws)
    logger.info("browser_notification_client_registered", total=len(_ws_clients))


def unregister_client(ws):
    """Unregister a WebSocket client."""
    _ws_clients.discard(ws)
    logger.info("browser_notification_client_unregistered", total=len(_ws_clients))


def normalize_status(status_data: dict) -> dict[str, Any]:
    """Normalize worker/API status payloads for the frontend."""
    connected = bool(status_data.get("deriv_connected", status_data.get("connected", False)))
    return {
        "connected": connected,
        "deriv_connected": connected,
        "ticks_collected": int(status_data.get("ticks_collected") or 0),
        "quotes_collected": int(status_data.get("quotes_collected") or 0),
        "symbol": status_data.get("symbol"),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def get_latest_status() -> dict[str, Any]:
    """Return the most recent worker status snapshot."""
    return dict(_latest_status)


async def send_notification(event_type: str, data: dict):
    """
    Send a notification to all connected browser clients.
    Falls back gracefully if no clients are connected.
    """
    if not _ws_clients:
        return

    message = json.dumps({
        "type": event_type,
        "data": data,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    disconnected = set()
    for ws in _ws_clients.copy():
        try:
            await ws.send_text(message)
        except Exception:
            disconnected.add(ws)

    for ws in disconnected:
        _ws_clients.discard(ws)


async def send_signal_notification(signal_data: dict):
    """Send a signal alert notification."""
    await send_notification("signal_alert", signal_data)


async def send_status_update(status_data: dict):
    """Store + broadcast a status update to browser clients."""
    global _latest_status
    normalized = normalize_status(status_data)
    _latest_status = normalized
    await send_notification("status_update", normalized)
