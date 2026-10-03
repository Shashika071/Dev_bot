"""
Websocket endpoints for browser push notifications and real-time status.
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
import structlog

from app.notifications.browser import register_client, unregister_client

logger = structlog.get_logger(__name__)
router = APIRouter(tags=["ws"])


async def _handle_ws_client(websocket: WebSocket, endpoint: str):
    """Shared handler: accept, register, keep-alive, and cleanup."""
    await websocket.accept()
    register_client(websocket)
    try:
        while True:
            # Keep connection open; wait for client disconnect or any ping text
            await websocket.receive_text()
    except WebSocketDisconnect:
        unregister_client(websocket)
    except Exception as e:
        logger.error("ws_error", endpoint=endpoint, error=str(e))
        unregister_client(websocket)


@router.websocket("/ws/status")
async def websocket_status(websocket: WebSocket):
    """WebSocket endpoint for real-time worker status updates (used by dashboard)."""
    await _handle_ws_client(websocket, "/ws/status")


@router.websocket("/ws/notifications")
async def websocket_notifications(websocket: WebSocket):
    """WebSocket endpoint for signal alert notifications."""
    await _handle_ws_client(websocket, "/ws/notifications")
