"""
Background worker entrypoint.

Forex Rise/Fall scanning runs inside the API service. This process stays idle
so existing compose stacks keep a healthy worker container without legacy Touch
collection or model inference.
"""

import asyncio

import structlog

from app.config import settings

logger = structlog.get_logger(__name__)


async def worker_loop():
    logger.info(
        "worker_idle",
        bot_mode=settings.bot_mode,
        reason="Forex scanner runs in the API service; Touch worker retired",
    )
    while True:
        await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(worker_loop())
