"""WebSocket client stores subscriptions for reconnect replay."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.deriv.client import DerivWSClient


@pytest.mark.asyncio
async def test_subscription_persisted_and_replayed():
    client = DerivWSClient()
    client._connected = True
    client.ws = MagicMock()
    client.ws.send = AsyncMock()

    handler = AsyncMock()
    await client.send_and_subscribe(
        {"ticks": "R_100"},
        handler,
        stream_msg_type="tick",
    )

    assert len(client._subscriptions) == 1
    assert client._subscriptions[0][0] == {"ticks": "R_100"}
    assert "tick" in client._handlers

    client.ws.send.reset_mock()
    await client._resubscribe()
    assert client.ws.send.await_count == 1
    sent = client.ws.send.await_args.args[0]
    assert "ticks" in sent
    assert "subscribe" in sent


@pytest.mark.asyncio
async def test_duplicate_subscribe_dedupes():
    client = DerivWSClient()
    client._connected = True
    client.ws = MagicMock()
    client.ws.send = AsyncMock()
    h = AsyncMock()

    await client.send_and_subscribe({"ticks": "R_100"}, h, stream_msg_type="tick")
    await client.send_and_subscribe({"ticks": "R_100"}, h, stream_msg_type="tick")
    assert len(client._subscriptions) == 1
