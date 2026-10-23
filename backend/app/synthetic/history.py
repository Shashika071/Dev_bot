"""Bounded, public Deriv history for the R_100 one-minute strategy."""
from __future__ import annotations

import asyncio
import copy
import math
import time
from functools import lru_cache
from pathlib import Path

import pandas as pd

from app.deriv.client import DerivWSClient

SYMBOL = "R_100"
TIMEFRAMES = {"1m": 60, "5m": 300, "15m": 900}
DEFAULT_DAYS = 90
MIN_DAYS = 60
MAX_DAYS = 90
PAGE_COUNT = 1000
PAGE_PAUSE_SECONDS = 1.5
RATE_LIMIT_BACKOFFS = (2, 5, 10)
OHLC_COLUMNS = ["epoch", "open", "high", "low", "close"]


def history_root(model_base: str | Path) -> Path:
    return Path(model_base) / "synthetic_r100" / "history"


def clean_candles(rows: list[dict], step: int, *, now: int | None = None) -> list[dict]:
    """Keep unique, aligned, valid and fully closed candles; never fill gaps."""
    cutoff = int(time.time()) if now is None else int(now)
    valid: dict[int, dict] = {}
    for row in rows:
        try:
            epoch_value = float(row["epoch"])
            if not math.isfinite(epoch_value) or not epoch_value.is_integer():
                continue
            epoch = int(epoch_value)
            values = {name: float(row[name]) for name in OHLC_COLUMNS[1:]}
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if epoch < 0 or epoch % step or epoch + step > cutoff:
            continue
        if not all(math.isfinite(value) and value > 0 for value in values.values()):
            continue
        if not (values["low"] <= min(values["open"], values["close"])
                <= max(values["open"], values["close"]) <= values["high"]):
            continue
        valid[epoch] = {"epoch": epoch, **values}
    return [valid[epoch] for epoch in sorted(valid)]


def load_candles(root: Path, tf: str, *, now: int | None = None) -> list[dict]:
    path = root / f"{tf}.parquet"
    if not path.exists():
        return []
    frame = pd.read_parquet(path)
    return clean_candles(frame.to_dict("records"), TIMEFRAMES[tf], now=now)


def save_candles(root: Path, tf: str, rows: list[dict], *,
                 now: int | None = None, earliest: int | None = None) -> list[dict]:
    root.mkdir(parents=True, exist_ok=True)
    merged = clean_candles(load_candles(root, tf, now=now) + rows, TIMEFRAMES[tf], now=now)
    if earliest is not None:
        merged = [row for row in merged if row["epoch"] >= earliest]
    target = root / f"{tf}.parquet"
    temporary = root / f"{tf}.parquet.tmp"
    pd.DataFrame(merged, columns=OHLC_COLUMNS).to_parquet(temporary, index=False)
    temporary.replace(target)
    return merged


def load_frames(model_base: str | Path, *, now: int | None = None) -> dict[str, list[dict]]:
    root = history_root(model_base)
    return {tf: load_candles(root, tf, now=now) for tf in TIMEFRAMES}


async def repair_gaps(model_base: str | Path, client, *, max_pages: int = 30) -> dict:
    """Recover only absent candles from public history; preserve existing OHLC."""
    root = history_root(model_base)
    now = int(time.time())
    report = {}
    for tf, step in TIMEFRAMES.items():
        rows = load_candles(root, tf, now=now)
        missing = {epoch for a, b in zip(rows, rows[1:])
                   for epoch in range(a["epoch"] + step, b["epoch"], step)}
        before = len(missing)
        recovered = []
        attempted = set()
        for _ in range(max_pages):
            remaining = missing - attempted
            if not remaining:
                break
            first = min(remaining)
            end = min(first + (PAGE_COUNT - 1) * step, max(remaining)) + step - 1
            batch = await client.get_candles(SYMBOL, granularity=step, count=PAGE_COUNT, end=end)
            for row in clean_candles(batch, step, now=now):
                if row["epoch"] in missing:
                    recovered.append(row)
                    missing.remove(row["epoch"])
            attempted.update(epoch for epoch in remaining if first <= epoch <= end)
            await asyncio.sleep(0.25)
        if recovered:
            await asyncio.to_thread(save_candles, root, tf, recovered, now=now)
        report[tf] = {"missing_before": before, "recovered": len(recovered), "missing_after": len(missing)}
    return report


def span_summary(rows: list[dict], step: int) -> dict:
    if not rows:
        return {"bars": 0, "from_epoch": None, "to_epoch": None, "days": 0.0, "gaps": 0}
    first, last = int(rows[0]["epoch"]), int(rows[-1]["epoch"])
    gaps = sum(max(0, (int(b["epoch"]) - int(a["epoch"])) // step - 1)
               for a, b in zip(rows, rows[1:]))
    return {
        "bars": len(rows), "from_epoch": first, "to_epoch": last,
        "days": round((last + step - first) / 86400, 2), "gaps": gaps,
    }


@lru_cache(maxsize=16)
def _history_summary(root_path: str, stamp: tuple, minute: int) -> dict:
    """Summary reads are shared across polls until the candle files change."""
    root = Path(root_path)
    frames = {}
    for tf, step in TIMEFRAMES.items():
        try:
            frames[tf] = span_summary(load_candles(root, tf), step)
        except Exception as exc:
            frames[tf] = {**span_summary([], step), "error": f"{type(exc).__name__}: {exc}"}
    ready = all(summary["days"] >= MIN_DAYS - 1
                and summary["bars"] >= int(MIN_DAYS * 86400 / TIMEFRAMES[tf] * 0.95)
                for tf, summary in frames.items())
    return {
        "symbol": SYMBOL, "timeframes": frames, "ready_for_train": ready,
        "minimum_days": MIN_DAYS, "maximum_days": MAX_DAYS,
        "path": str(root), "source": "Deriv public candles",
    }


def history_status(model_base: str | Path, job: dict | None = None) -> dict:
    root = history_root(model_base)
    stamp = []
    for tf in TIMEFRAMES:
        try:
            stat = (root / f"{tf}.parquet").stat()
            stamp.append((tf, stat.st_mtime_ns, stat.st_size))
        except OSError:
            stamp.append((tf, None, None))
    summary = copy.deepcopy(_history_summary(str(root), tuple(stamp), int(time.time()) // 60))
    return {**summary, "job": dict(job or {"status": "idle", "progress": "", "error": None})}


def validate_days(days: int) -> int:
    if isinstance(days, bool) or not isinstance(days, int) or not MIN_DAYS <= days <= MAX_DAYS:
        raise ValueError(f"History days must be an integer from {MIN_DAYS} to {MAX_DAYS}")
    return days


async def _page(client: DerivWSClient, step: int, end: int, progress: dict) -> list[dict]:
    for attempt in range(len(RATE_LIMIT_BACKOFFS) + 1):
        try:
            return await client.get_candles(SYMBOL, granularity=step, count=PAGE_COUNT, end=end)
        except Exception as exc:
            text = str(exc).lower()
            limited = any(token in text for token in ("ratelimit", "rate limit", "ticks_history_expensive"))
            if not limited or attempt == len(RATE_LIMIT_BACKOFFS):
                raise
            delay = RATE_LIMIT_BACKOFFS[attempt]
            progress["progress"] = f"Deriv rate limit; retrying in {delay}s"
            await asyncio.sleep(delay)
    return []


async def download_history(model_base: str | Path, *, days: int = DEFAULT_DAYS,
                           progress: dict | None = None) -> dict:
    """Download every timeframe directly, paging backwards within 60–90 days.

    Existing data is merged after each page so interrupted runs are recoverable.
    Rewalking the bounded interval also repairs gaps in older partial downloads.
    """
    days = validate_days(days)
    report = progress if progress is not None else {}
    report.update(status="running", progress="Connecting to Deriv public history",
                  error=None, started_at=int(time.time()), requested_days=days)
    root = history_root(model_base)
    client = DerivWSClient()
    now = int(time.time())
    try:
        await client.connect()
        for tf in ("15m", "5m", "1m"):
            step = TIMEFRAMES[tf]
            earliest = now // step * step - days * 86400
            end = now
            oldest = now + 1
            page_limit = math.ceil(days * 86400 / step / PAGE_COUNT) + 3
            for page in range(page_limit):
                report["progress"] = f"{tf}: downloading page {page + 1} for {days} days"
                batch = await _page(client, step, end, report)
                valid = clean_candles(batch, step, now=now)
                if not valid:
                    break
                first = int(valid[0]["epoch"])
                if first >= oldest:
                    break
                saved = await asyncio.to_thread(save_candles, root, tf, valid, now=now, earliest=earliest)
                report["progress"] = f"{tf}: saved {len(saved):,} closed candles"
                oldest = first
                if first <= earliest:
                    break
                end = first - 1
                await asyncio.sleep(PAGE_PAUSE_SECONDS)
        status = await asyncio.to_thread(history_status, model_base)
        if not status["ready_for_train"]:
            raise ValueError("Deriv returned insufficient history; need at least 60 days on all three timeframes")
        report.update(status="done", progress="Public history download complete",
                      error=None, finished_at=int(time.time()))
    except Exception as exc:
        report.update(status="error", progress="Download incomplete; saved candles retained",
                      error=f"{type(exc).__name__}: {exc}", finished_at=int(time.time()))
        raise
    finally:
        await client.disconnect()
    return await asyncio.to_thread(history_status, model_base, report)
