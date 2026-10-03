"""
Historical tick downloader via Deriv public WebSocket.

Deriv's public Options WS returns ~1000 ticks per request ending at `end`.
We page backward until we cover the requested number of days, then upsert
into PostgreSQL.

IMPORTANT LIMITATION:
The public endpoint typically retains only ~24 hours of tick history for
Volatility indices. Requests older than that often "wrap" and return the
latest ticks again. When that happens we stop and report the API floor
clearly — this is not a save bug.
"""

import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional, Callable, Awaitable

import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.database import async_session
from app.models.tick import Tick
from app.deriv.client import DerivWSClient

logger = structlog.get_logger(__name__)

MAX_TICKS_PER_REQUEST = 1000
RATE_LIMIT_PAUSE = 0.35
# If returned newest is this far ahead of requested end, treat as API wrap
WRAP_DETECT_SECONDS = 3600

# Rough public-API yield used for UI estimates / lookback sizing
TICKS_PER_DAY = {
    "R_100": 43_000,      # ~2 ticks/sec average
    "1HZ100V": 86_400,    # 1 tick/sec
}


def estimate_ticks_per_day(symbol: str) -> int:
    return TICKS_PER_DAY.get(symbol, 43_000)


async def fetch_ticks_chunk(
    client: DerivWSClient,
    symbol: str,
    end_epoch: int,
    count: int = MAX_TICKS_PER_REQUEST,
) -> list[dict]:
    """Fetch one history chunk ending at end_epoch. Returns {epoch, price} list."""
    response = await client.get_ticks_history(
        symbol=symbol,
        end=end_epoch,
        count=count,
        style="ticks",
    )
    if "error" in response:
        err = response["error"]
        # Common when asking beyond retained history
        raise RuntimeError(
            err.get("message", str(err)) if isinstance(err, dict) else str(err)
        )

    history = response.get("history", {})
    times = history.get("times", [])
    prices = history.get("prices", [])
    return [{"epoch": int(e), "price": float(p)} for e, p in zip(times, prices)]


async def fetch_ticks_http(
    symbol: str,
    start_epoch: int,
    end_epoch: int,
    app_id: str = "36544",
    client: Optional[DerivWSClient] = None,
) -> list[dict]:
    del start_epoch, app_id
    own_client = client is None
    if own_client:
        client = DerivWSClient()
        await client.connect()
    try:
        return await fetch_ticks_chunk(client, symbol, end_epoch)
    finally:
        if own_client:
            await client.disconnect()


async def download_and_store_ticks(
    symbol: str,
    days_back: int = 7,
    app_id: str = "36544",
    on_progress: Optional[Callable[[str], Awaitable[None]]] = None,
    target_ticks: Optional[int] = None,
) -> int:
    """
    Download historical ticks (paging backward) and store them to PostgreSQL.

    Prefer `target_ticks` (stop after N newly saved ticks). `days_back` is the
    maximum lookback window. Stops cleanly at Deriv public history floor (~1 day).
    """
    del app_id

    per_day = estimate_ticks_per_day(symbol)
    if target_ticks and target_ticks > 0:
        # Size lookback from requested ticks (+1 day buffer), keep a hard cap
        needed_days = max(1, int((target_ticks + per_day - 1) / per_day) + 1)
        days_back = max(days_back, min(needed_days, 7))

    end_dt = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=days_back)
    target_start = int(start_dt.timestamp())
    current_end = int(end_dt.timestamp())

    total_stored = 0
    chunk_num = 0
    seen_oldest: Optional[int] = None
    stop_reason = "completed requested window"

    async def _log(msg: str):
        logger.info("history_download_progress", msg=msg)
        if on_progress:
            await on_progress(msg)

    goal = f"target={target_ticks:,} new ticks" if target_ticks else f"{days_back}d window"
    await _log(
        f"Starting history download: {symbol} ({goal}) "
        f"lookback≤{days_back}d · ≈{per_day:,} ticks/day typical. "
        f"Public API usually only keeps ~24h."
    )

    client = DerivWSClient()
    await client.connect()

    try:
        while current_end > target_start:
            chunk_num += 1
            try:
                if not client.connected:
                    await client.ensure_connected()

                raw = await fetch_ticks_chunk(client, symbol, current_end)
                if not raw:
                    stop_reason = "API returned empty history (history floor reached)"
                    await _log(f"No more data at chunk {chunk_num}. {stop_reason}.")
                    break

                oldest = min(r["epoch"] for r in raw)
                newest = max(r["epoch"] for r in raw)

                # Detect wrap: asked for old end, API returned recent ticks instead
                if newest > current_end + WRAP_DETECT_SECONDS:
                    floor_txt = (
                        datetime.fromtimestamp(seen_oldest, tz=timezone.utc).isoformat()
                        if seen_oldest
                        else "unknown"
                    )
                    stop_reason = (
                        "Deriv public API history floor reached "
                        f"(~24h of ticks). Oldest kept ≈ {floor_txt}. "
                        "Keep the live worker running to accumulate more days."
                    )
                    await _log(stop_reason)
                    break

                if seen_oldest is not None and oldest >= seen_oldest:
                    stop_reason = (
                        "No further historical progress — public API will not go older. "
                        "Typical limit is about 1 day of ticks."
                    )
                    await _log(stop_reason)
                    break

                # Keep only ticks inside the requested window
                in_window = [r for r in raw if r["epoch"] >= target_start]
                if not in_window:
                    stop_reason = "Reached requested start time"
                    await _log(stop_reason)
                    break

                ticks = [
                    {
                        "symbol": symbol,
                        "epoch": r["epoch"],
                        "tick_time": datetime.fromtimestamp(r["epoch"], tz=timezone.utc),
                        "quote": r["price"],
                        "is_gap": 0,
                    }
                    for r in in_window
                ]

                async with async_session() as db:
                    stmt = (
                        pg_insert(Tick)
                        .values(ticks)
                        .on_conflict_do_nothing(index_elements=["symbol", "epoch"])
                        .returning(Tick.id)
                    )
                    result = await db.execute(stmt)
                    inserted = len(result.fetchall())
                    await db.commit()

                total_stored += inserted
                seen_oldest = oldest

                goal_txt = f"/{target_ticks:,}" if target_ticks else ""
                await _log(
                    f"Chunk {chunk_num}: fetched={len(ticks):,} new_saved={inserted:,} "
                    f"(total_new={total_stored:,}{goal_txt}) "
                    f"{datetime.fromtimestamp(oldest, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')} -> "
                    f"{datetime.fromtimestamp(newest, tz=timezone.utc).strftime('%H:%M UTC')}"
                )

                if target_ticks and total_stored >= target_ticks:
                    stop_reason = f"Reached target of {target_ticks:,} new ticks"
                    await _log(stop_reason)
                    break

                current_end = oldest - 1
                await asyncio.sleep(RATE_LIMIT_PAUSE)

            except Exception as e:
                logger.error("history_download_chunk_error", chunk=chunk_num, error=str(e))
                await _log(f"Chunk {chunk_num} error: {e} — retrying in 5s...")
                await asyncio.sleep(5)
                try:
                    await client.force_reconnect()
                except Exception:
                    pass
    finally:
        await client.disconnect()

    await _log(
        f"Download complete. New ticks saved: {total_stored:,}. Stop reason: {stop_reason}"
    )
    return total_stored
