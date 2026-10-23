"""Download public R_100 ticks. An unfinished window is marked partial and is not filled."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pandas as pd

from app.trading_ml.data.ticks import parse_tick_payload

SYMBOL = "R_100"


def validate_hours(hours: int) -> int:
    if type(hours) is not int or not 1 <= hours <= 48:
        raise ValueError("Tick history must be an integer from 1 to 48 hours")
    return hours


async def download_ticks(
    path: str | Path,
    *,
    hours: int,
    client,
    end: int | None = None,
    pause_seconds: float = 0,
    progress: dict | None = None,
) -> dict:
    """Page backward until the requested window is covered or Deriv returns no ticks."""
    hours = validate_hours(hours)
    cursor = int(time.time()) if end is None else int(end)
    start = cursor - hours * 3600
    stored: dict[int, float] = {}
    partial = False
    while cursor > start:
        if progress is not None:
            progress["progress"] = f"Downloading R_100 ticks through {cursor}"
        payload = await client.get_ticks_history(SYMBOL, end=cursor, count=5000, style="ticks")
        batch = parse_tick_payload(payload)
        fresh = [
            tick for tick in batch
            if start <= tick["epoch"] < cursor and tick["epoch"] not in stored
        ]
        if not fresh:
            partial = cursor > start
            break
        for tick in fresh:
            stored[tick["epoch"]] = tick["quote"]
        oldest = min(tick["epoch"] for tick in fresh)
        if oldest <= start:
            break
        cursor = oldest
        if pause_seconds:
            await asyncio.sleep(pause_seconds)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [{"epoch": epoch, "quote": stored[epoch]} for epoch in sorted(stored)]
    ).to_parquet(target, index=False)
    return {"ticks": len(stored), "partial": partial, "hours": hours, "start": start, "end": end}
