"""Paginated Deriv candle history for EURUSD research/training."""
from __future__ import annotations

import asyncio
import math
import time
from pathlib import Path

import pandas as pd

from app.deriv.client import DerivWSClient
from app.forex.policy import TIMEFRAMES

ML_SYMBOL = "frxEURUSD"
# Download coarser TFs first (fewer pages) so a rate-limit mid-run still leaves 1h/4h usable.
HISTORY_TFS = ("4h", "1h", "15m")
# ~3 years of calendar seconds; Deriv may return less.
TARGET_SECONDS = 3 * 365 * 86400
# Ready threshold used by training gate.
MIN_BARS_READY = 500
PAGE_COUNT = 1000
PAGE_PAUSE_SECONDS = 1.75
TF_PAUSE_SECONDS = 8.0
RATE_LIMIT_BACKOFFS = (12, 25, 45, 75, 120)


def history_root(model_dir: str | Path) -> Path:
    return Path(model_dir).parent / "forex" / "history" / ML_SYMBOL


def parquet_path(root: Path, tf: str) -> Path:
    return root / f"{tf}.parquet"


def load_candles(root: Path, tf: str) -> list[dict]:
    path = parquet_path(root, tf)
    if not path.exists():
        return []
    frame = pd.read_parquet(path)
    if frame.empty:
        return []
    rows = frame.sort_values("epoch").drop_duplicates("epoch", keep="last")
    return rows[["epoch", "open", "high", "low", "close"]].to_dict("records")


def save_candles(root: Path, tf: str, rows: list[dict]) -> list[dict]:
    root.mkdir(parents=True, exist_ok=True)
    existing = load_candles(root, tf)
    merged = {int(row["epoch"]): {
        "epoch": int(row["epoch"]),
        "open": float(row["open"]),
        "high": float(row["high"]),
        "low": float(row["low"]),
        "close": float(row["close"]),
    } for row in existing + rows if row and math.isfinite(float(row.get("close", float("nan"))))}
    ordered = [merged[epoch] for epoch in sorted(merged)]
    pd.DataFrame(ordered).to_parquet(parquet_path(root, tf), index=False)
    return ordered


def span_summary(rows: list[dict]) -> dict:
    if not rows:
        return {"bars": 0, "from_epoch": None, "to_epoch": None, "days": 0.0}
    first, last = int(rows[0]["epoch"]), int(rows[-1]["epoch"])
    return {"bars": len(rows), "from_epoch": first, "to_epoch": last,
            "days": round((last - first) / 86400, 2)}


def history_status(model_dir: str | Path, job: dict | None = None) -> dict:
    root = history_root(model_dir)
    # Report in the UI-friendly order.
    frames = {tf: span_summary(load_candles(root, tf)) for tf in ("15m", "1h", "4h")}
    ready = all(frames[tf]["bars"] >= MIN_BARS_READY for tf in ("15m", "1h", "4h"))
    return {
        "symbol": ML_SYMBOL,
        "timeframes": frames,
        "ready_for_train": ready,
        "path": str(root),
        "job": job or {"status": "idle", "progress": "", "error": None},
    }


def aggregate_candles(rows: list[dict], granularity: int, *, source_step: int = 900) -> list[dict]:
    """Build higher-TF OHLC from finer candles (local fallback when Deriv rate-limits).

    Incomplete buckets (missing source bars) are dropped so a single M15 cannot
    become a full H1/H4 candle.
    """
    expected = max(1, int(granularity // max(1, source_step)))
    buckets: dict[int, dict] = {}
    counts: dict[int, int] = {}
    for row in sorted(rows, key=lambda item: int(item["epoch"])):
        epoch = int(row["epoch"])
        if epoch % source_step != 0:
            continue
        bucket = epoch - (epoch % granularity)
        bar = buckets.get(bucket)
        counts[bucket] = counts.get(bucket, 0) + 1
        if bar is None:
            buckets[bucket] = {
                "epoch": bucket,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
            }
        else:
            bar["high"] = max(bar["high"], float(row["high"]))
            bar["low"] = min(bar["low"], float(row["low"]))
            bar["close"] = float(row["close"])
    return [buckets[epoch] for epoch in sorted(buckets) if counts.get(epoch, 0) >= expected]


def _is_rate_limit(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "ratelimit" in text or "rate limit" in text or "ticks_history_expensive" in text


async def _get_candles_with_backoff(
    client: DerivWSClient,
    *,
    granularity: int,
    end: int | str,
    progress: dict,
    label: str,
) -> list[dict]:
    last_exc: BaseException | None = None
    for attempt, wait in enumerate((0, *RATE_LIMIT_BACKOFFS), start=1):
        if wait:
            progress["progress"] = f"{label}: rate-limited, waiting {wait}s (try {attempt})"
            await asyncio.sleep(wait)
        try:
            return await client.get_candles(
                ML_SYMBOL, granularity=granularity, count=PAGE_COUNT, end=end)
        except Exception as exc:
            last_exc = exc
            if _is_rate_limit(exc):
                continue
            raise
    assert last_exc is not None
    raise last_exc


async def _download_timeframe(
    client: DerivWSClient,
    root: Path,
    tf: str,
    *,
    progress: dict,
) -> list[dict]:
    step = TIMEFRAMES[tf]
    stored = load_candles(root, tf)
    target = int(time.time()) - TARGET_SECONDS
    pages = 0

    # Always refresh the latest tip, then only walk older than what we already have.
    progress["progress"] = f"{tf}: refreshing latest tip, bars={len(stored)}"
    tip = await _get_candles_with_backoff(
        client, granularity=step, end="latest", progress=progress, label=tf)
    if tip:
        stored = save_candles(root, tf, tip)
    await asyncio.sleep(PAGE_PAUSE_SECONDS)

    if stored and int(stored[0]["epoch"]) <= target:
        progress["progress"] = f"{tf}: already deep enough ({span_summary(stored)['days']} days)"
        return stored

    end: int | str = int(stored[0]["epoch"]) - 1 if stored else "latest"
    oldest_seen = int(stored[0]["epoch"]) if stored else None

    while True:
        progress["progress"] = f"{tf}: page {pages + 1}, bars={len(stored)}"
        batch = await _get_candles_with_backoff(
            client, granularity=step, end=end, progress=progress, label=tf)
        if not batch:
            break
        stored = save_candles(root, tf, batch)
        pages += 1
        first = int(batch[0]["epoch"])
        if oldest_seen is not None and first >= oldest_seen:
            break
        oldest_seen = first
        if first <= target:
            break
        end = first - 1
        await asyncio.sleep(PAGE_PAUSE_SECONDS)
        if pages >= 400:
            break
    return stored


def _fill_from_15m(root: Path, progress: dict) -> None:
    """If 1h/4h are missing after a rate-limit, synthesize them from saved 15m."""
    m15 = load_candles(root, "15m")
    if len(m15) < MIN_BARS_READY:
        return
    for tf, step in (("1h", 3600), ("4h", 14400)):
        existing = load_candles(root, tf)
        if len(existing) >= MIN_BARS_READY:
            continue
        progress["progress"] = f"{tf}: building from 15m ({len(m15)} bars)"
        aggregated = aggregate_candles(m15, step)
        if aggregated:
            save_candles(root, tf, aggregated)


async def download_history(model_dir: str | Path, *, progress: dict | None = None) -> dict:
    """Page backward until ~3 years or the feed stops advancing.

    Resumes from stored parquet, backs off on Deriv rate limits, and can fill
    1h/4h from 15m when the expensive history endpoint throttles.
    """
    root = history_root(model_dir)
    client = DerivWSClient()
    report = progress if progress is not None else {}
    report.update(status="running", progress="Connecting to Deriv", error=None, started_at=int(time.time()))
    rate_limited = False
    try:
        await client.connect()
        for index, tf in enumerate(HISTORY_TFS):
            try:
                await _download_timeframe(client, root, tf, progress=report)
            except Exception as exc:
                if _is_rate_limit(exc):
                    rate_limited = True
                    report["progress"] = (
                        f"{tf}: Deriv rate limit — keeping saved bars; "
                        f"will fill missing TFs from 15m if possible"
                    )
                    report["error"] = f"RateLimit while downloading {tf}: {exc}"
                    break
                raise
            if index < len(HISTORY_TFS) - 1:
                report["progress"] = f"{tf} done — pausing {int(TF_PAUSE_SECONDS)}s before next TF"
                await asyncio.sleep(TF_PAUSE_SECONDS)
        _fill_from_15m(root, report)
        status = history_status(model_dir, report)
        if rate_limited and not status["ready_for_train"]:
            report.update(
                status="error",
                progress="Partial download — wait a few minutes and click Download again",
                finished_at=int(time.time()),
            )
        elif rate_limited:
            report.update(
                status="done",
                progress="History ready (1h/4h filled from 15m after rate limit)",
                error=None,
                finished_at=int(time.time()),
            )
        else:
            report.update(status="done", progress="History download finished",
                          error=None, finished_at=int(time.time()))
    except Exception as exc:
        _fill_from_15m(root, report)
        status = history_status(model_dir, report)
        if status["ready_for_train"]:
            report.update(
                status="done",
                progress="History ready via 15m aggregation after download error",
                error=None,
                finished_at=int(time.time()),
            )
        else:
            report.update(status="error", error=f"{type(exc).__name__}: {exc}",
                          progress="Download failed", finished_at=int(time.time()))
            raise
    finally:
        await client.disconnect()
    return history_status(model_dir, report)
