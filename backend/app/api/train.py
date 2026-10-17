"""
Training API — triggers the ML model training pipeline via HTTP.
Fetches stored tick data from the database and runs the full training orchestrator.
"""

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db, async_session
from app.features.tick_loader import load_ticks_dataframe, tick_coverage_stats
from app.ml.history_manifest import prefer_longer_history
from app.ml.registry import promote_model_version, save_model_version
from app.ml.trainer import TrainingOrchestrator
from app.models.quote import Quote
from app.models.tick import Tick
from app.collector.http_history import download_and_store_ticks

router = APIRouter(prefix="/train", tags=["training"])
logger = structlog.get_logger(__name__)

# Simple in-memory training state (single-process, single container)
_training_state: dict = {
    "status": "idle",        # idle | running | done | error
    "progress": "",
    "result": None,
    "error": None,
}


def _prefs_path() -> str:
    return os.path.join(settings.model_dir, "train_ui_prefs.json")


def _default_train_prefs() -> dict:
    return {
        "min_calibration_samples": int(settings.train_min_calibration_samples),
        "min_edge_selected": int(settings.train_edge_min_selected),
        "auto_raise": True,
        "note": (
            "These prefs are owned by the Train UI after deploy. "
            ".env values are only first-boot defaults — change levels in the UI, not on the VPS."
        ),
    }


def load_train_prefs() -> dict:
    path = _prefs_path()
    base = _default_train_prefs()
    try:
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                base.update({k: saved[k] for k in saved if k in base or k in (
                    "min_calibration_samples", "min_edge_selected", "auto_raise",
                )})
    except Exception as e:
        logger.warning("train_prefs_load_failed", error=str(e))
    base["min_calibration_samples"] = max(11, min(200, int(base["min_calibration_samples"])))
    base["min_edge_selected"] = max(5, min(200, int(base["min_edge_selected"])))
    base["auto_raise"] = bool(base.get("auto_raise", True))
    return base


def save_train_prefs(prefs: dict) -> dict:
    merged = _default_train_prefs()
    merged.update(prefs or {})
    merged["min_calibration_samples"] = max(11, min(200, int(merged["min_calibration_samples"])))
    merged["min_edge_selected"] = max(5, min(200, int(merged["min_edge_selected"])))
    merged["auto_raise"] = bool(merged.get("auto_raise", True))
    os.makedirs(settings.model_dir, exist_ok=True)
    path = _prefs_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2)
    return merged


class TrainPrefsBody(BaseModel):
    min_calibration_samples: int = Field(20, ge=11, le=200)
    min_edge_selected: int = Field(15, ge=5, le=200)
    auto_raise: bool = True


@router.get("/prefs")
async def get_train_prefs() -> dict:
    """UI-owned training strictness prefs (survives refresh; no .env edit needed)."""
    prefs = load_train_prefs()
    return {
        **prefs,
        "source": "ui_prefs_file",
        "env_is_default_only": True,
    }


@router.put("/prefs")
async def put_train_prefs(body: TrainPrefsBody) -> dict:
    """Save Train UI strictness — call this instead of editing .env.prod."""
    saved = save_train_prefs(body.model_dump())
    return {**saved, "source": "ui_prefs_file", "env_is_default_only": True}


@router.get("/status")
async def training_status() -> dict:
    """Get current training job status."""
    return _training_state


def _model_row_from_meta(
    meta: dict,
    *,
    is_latest: bool = False,
    meta_path: str | None = None,
) -> dict:
    from app.ml.promotion import promotion_blockers

    cal = meta.get("calibrator_path")
    paths = meta.get("paths") or {}
    loadable = bool(cal and os.path.exists(cal))
    blockers = promotion_blockers(meta)
    return {
        "direction": meta.get("direction"),
        "selected_pipeline": meta.get("selected_pipeline"),
        "has_demonstrated_edge": bool(meta.get("has_demonstrated_edge", False)),
        "edge_description": meta.get("edge_description"),
        "version_tag": meta.get("version_tag"),
        "symbol": meta.get("symbol"),
        "barrier_distance": meta.get("barrier_distance"),
        "barrier_unit": meta.get("barrier_unit"),
        "duration_seconds": meta.get("duration_seconds"),
        "created_at": meta.get("created_at"),
        "metrics": meta.get("metrics") or {},
        "is_latest": is_latest,
        "is_active": False,
        "promotion_ready": bool(meta.get("promotion_ready")) and not blockers,
        "promotion_blockers": blockers,
        "loadable": loadable,
        "meta_path": meta_path,
        "paths_present": {
            k: bool(v and os.path.exists(v))
            for k, v in paths.items()
        },
    }


def _safe_unlink(path: str | None) -> bool:
    if not path or not isinstance(path, str):
        return False
    try:
        if os.path.isfile(path):
            os.remove(path)
            return True
    except Exception:
        pass
    return False


def _retarget_latest(direction: str, model_dir: str) -> None:
    """Point latest_{direction}.json at newest remaining meta, or remove it."""
    import glob
    import json

    latest_path = os.path.join(model_dir, f"latest_{direction}.json")
    metas = []
    for path in glob.glob(os.path.join(model_dir, f"meta_{direction}_*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                meta = json.load(f)
            metas.append((str(meta.get("created_at") or ""), path, meta))
        except Exception:
            continue
    if not metas:
        _safe_unlink(latest_path)
        return
    metas.sort(key=lambda x: x[0], reverse=True)
    _, _, meta = metas[0]
    with open(latest_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, default=str)


@router.get("/models")
async def list_trained_models(db: AsyncSession = Depends(get_db)) -> dict:
    """
    List saved models for the dashboard picker.
    Includes every distinct (direction, barrier) from meta_*.json (not only latest_*),
    so Cross can still pick an older 0.9 train after a newer 0.09 overwrite of latest_*.
    Registry supplies is_active / eligibility for promote.
    """
    import glob
    import json
    from app.config import settings
    from app.models.model_version import ModelVersion

    model_dir = settings.model_dir
    latest_tags: set[str] = set()
    for path in glob.glob(os.path.join(model_dir, "latest_*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                meta = json.load(f)
            tag = meta.get("version_tag")
            if tag:
                latest_tags.add(str(tag))
        except Exception:
            continue

    # Full history: every meta_* version (so old trains can be deleted too)
    by_tag: dict[str, dict] = {}
    for path in sorted(glob.glob(os.path.join(model_dir, "meta_*.json"))):
        try:
            with open(path, encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            continue
        try:
            float(meta.get("barrier_distance"))
        except (TypeError, ValueError):
            continue
        tag = str(meta.get("version_tag") or path)
        row = _model_row_from_meta(
            meta, is_latest=tag in latest_tags, meta_path=path
        )
        prev = by_tag.get(tag)
        if prev is None or str(row.get("created_at") or "") >= str(prev.get("created_at") or ""):
            by_tag[tag] = row

    # Include latest_* if its tag isn't already listed (orphan latest pointer)
    for path in sorted(glob.glob(os.path.join(model_dir, "latest_*.json"))):
        try:
            with open(path, encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            continue
        tag = str(meta.get("version_tag") or "")
        if not tag or tag in by_tag:
            if tag and tag in by_tag:
                by_tag[tag]["is_latest"] = True
            continue
        by_tag[tag] = _model_row_from_meta(meta, is_latest=True, meta_path=path)

    # Overlay registry active / eligibility state
    try:
        reg_rows = (await db.execute(select(ModelVersion))).scalars().all()
        for reg in reg_rows:
            tag = str(reg.version_tag or "")
            if not tag:
                continue
            row = by_tag.get(tag)
            if row is None:
                continue
            row["is_active"] = bool(reg.is_active)
            row["registry_id"] = int(reg.id)
            if reg.has_demonstrated_edge is not None:
                row["has_demonstrated_edge"] = bool(reg.has_demonstrated_edge)
    except Exception as exc:
        logger.warning("model_list_registry_overlay_failed", error=str(exc))

    models = sorted(
        by_tag.values(),
        key=lambda m: (
            0 if m.get("is_active") else 1,
            str(m.get("symbol") or ""),
            float(m.get("barrier_distance") or 0),
            str(m.get("direction") or ""),
            0 if m.get("is_latest") else 1,
            str(m.get("created_at") or ""),
        ),
    )
    barriers = sorted(
        {
            round(float(m["barrier_distance"]), 6)
            for m in models
            if m.get("barrier_distance") is not None and m.get("loadable")
        }
    )

    return {
        "trained": len(models) > 0,
        "models": models,
        "barriers": barriers,
        "min_ticks_to_train": 1000,
        "recommended_ticks": 5000,
    }


@router.delete("/models/{version_tag}")
async def delete_trained_model(
    version_tag: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Delete one trained model version (artefacts + all meta/latest pointers).
    Deactivates the registry row first so the worker unloads any cached pack.
    """
    import glob
    import json
    from fastapi import HTTPException
    from app.config import settings
    from app.ml.registry import deactivate_model_version

    tag = str(version_tag or "").strip()
    if not tag or "/" in tag or "\\" in tag or ".." in tag:
        raise HTTPException(status_code=400, detail="Invalid version_tag")

    try:
        await deactivate_model_version(db, tag)
    except Exception as exc:
        logger.warning("model_registry_deactivate_failed", version_tag=tag, error=str(exc))

    model_dir = settings.model_dir
    matches: list[tuple[str, dict]] = []
    for path in glob.glob(os.path.join(model_dir, "meta_*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                candidate = json.load(f)
            if str(candidate.get("version_tag") or "") == tag:
                matches.append((path, candidate))
        except Exception:
            continue
    for path in glob.glob(os.path.join(model_dir, "latest_*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                candidate = json.load(f)
            if str(candidate.get("version_tag") or "") == tag:
                matches.append((path, candidate))
        except Exception:
            continue

    if not matches:
        raise HTTPException(status_code=404, detail=f"Model {tag} not found")

    meta = matches[0][1]
    removed: list[str] = []
    # Collect artefact paths from every matching json (meta + latest copies)
    artefact_paths: set[str] = set()
    for _, m in matches:
        for p in (m.get("paths") or {}).values():
            if p:
                artefact_paths.add(str(p))
        for key in ("model_path", "calibrator_path"):
            if m.get(key):
                artefact_paths.add(str(m[key]))
    for p in artefact_paths:
        if _safe_unlink(p):
            removed.append(p)

    direction = str(meta.get("direction") or "")
    ts = str(meta.get("timestamp") or "")
    if direction and ts:
        report = os.path.join(model_dir, f"report_{direction}_{ts}.json")
        if _safe_unlink(report):
            removed.append(report)

    # Remove every meta_/latest_ file that still points at this version
    for path, _ in matches:
        if _safe_unlink(path):
            removed.append(path)
    # latest_* may still exist if we only matched meta first — wipe by tag again
    for path in glob.glob(os.path.join(model_dir, "latest_*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                candidate = json.load(f)
            if str(candidate.get("version_tag") or "") == tag and _safe_unlink(path):
                removed.append(path)
        except Exception:
            continue

    if direction:
        _retarget_latest(direction, model_dir)

    return {
        "ok": True,
        "deleted": tag,
        "direction": direction,
        "barrier_distance": meta.get("barrier_distance"),
        "removed_files": len(removed),
        "message": f"Deleted model {tag}",
    }


def _format_bytes(n: int) -> str:
    """Human-readable size (B / KB / MB / GB)."""
    size = float(max(0, int(n)))
    for unit, div in (("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)):
        if size >= div:
            val = size / div
            return f"{val:.2f} {unit}" if val < 10 or unit == "GB" else f"{val:.1f} {unit}"
    return f"{int(size)} B"


async def _ticks_storage_bytes(db: AsyncSession) -> dict:
    """Postgres on-disk size for ticks table + indexes (0 if unavailable)."""
    from sqlalchemy import text

    try:
        result = await db.execute(
            text(
                "SELECT "
                "pg_total_relation_size('ticks')::bigint AS total_bytes, "
                "pg_relation_size('ticks')::bigint AS table_bytes, "
                "pg_indexes_size('ticks')::bigint AS index_bytes"
            )
        )
        row = result.mappings().first()
        if not row:
            return {
                "storage_bytes": 0,
                "storage_table_bytes": 0,
                "storage_index_bytes": 0,
                "storage_human": "0 B",
            }
        total = int(row["total_bytes"] or 0)
        return {
            "storage_bytes": total,
            "storage_table_bytes": int(row["table_bytes"] or 0),
            "storage_index_bytes": int(row["index_bytes"] or 0),
            "storage_human": _format_bytes(total),
        }
    except Exception:
        return {
            "storage_bytes": None,
            "storage_table_bytes": None,
            "storage_index_bytes": None,
            "storage_human": None,
        }


class TickDeleteBody(BaseModel):
    """Delete ticks for a symbol in an inclusive calendar date range (UTC days)."""

    symbol: str = Field("R_100", min_length=1, max_length=32)
    from_date: str = Field(..., description="YYYY-MM-DD (UTC day start, inclusive)")
    to_date: str = Field(..., description="YYYY-MM-DD (UTC day end, inclusive)")
    dry_run: bool = Field(False, description="If true, only count matching ticks")


def _parse_utc_day(value: str, *, end_of_day: bool = False) -> datetime:
    raw = str(value or "").strip()[:10]
    try:
        day = datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid date '{value}' — use YYYY-MM-DD") from e
    if end_of_day:
        # Inclusive end: keep ticks until next day 00:00 exclusive
        from datetime import timedelta

        return day + timedelta(days=1)
    return day


@router.post("/ticks/delete")
async def delete_ticks(body: TickDeleteBody, db: AsyncSession = Depends(get_db)) -> dict:
    """
    Delete stored ticks for a symbol between from_date and to_date (UTC calendar days).
    Use dry_run=true first to see how many rows would be removed.
    """
    symbol = str(body.symbol or "R_100").strip()
    start = _parse_utc_day(body.from_date, end_of_day=False)
    end_exclusive = _parse_utc_day(body.to_date, end_of_day=True)
    if end_exclusive <= start:
        raise HTTPException(status_code=400, detail="to_date must be on or after from_date")

    # Safety: refuse absurd ranges that look like typos ( > 366 days )
    span_days = (end_exclusive - start).total_seconds() / 86400.0
    if span_days > 366:
        raise HTTPException(status_code=400, detail="Date range too large (max 366 days). Split the delete.")

    count_q = await db.execute(
        select(func.count())
        .select_from(Tick)
        .where(Tick.symbol == symbol)
        .where(Tick.tick_time >= start)
        .where(Tick.tick_time < end_exclusive)
    )
    match_count = int(count_q.scalar_one() or 0)

    if body.dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "symbol": symbol,
            "from_date": body.from_date[:10],
            "to_date": body.to_date[:10],
            "match_count": match_count,
            "deleted": 0,
            "message": f"{match_count:,} ticks would be deleted for {symbol}.",
        }

    if match_count == 0:
        storage = await _ticks_storage_bytes(db)
        return {
            "ok": True,
            "dry_run": False,
            "symbol": symbol,
            "from_date": body.from_date[:10],
            "to_date": body.to_date[:10],
            "match_count": 0,
            "deleted": 0,
            "message": "No ticks in that range.",
            **storage,
        }

    result = await db.execute(
        delete(Tick)
        .where(Tick.symbol == symbol)
        .where(Tick.tick_time >= start)
        .where(Tick.tick_time < end_exclusive)
    )
    await db.commit()
    deleted = int(result.rowcount or 0)
    storage = await _ticks_storage_bytes(db)
    logger.info(
        "ticks_deleted_by_range",
        symbol=symbol,
        from_date=body.from_date[:10],
        to_date=body.to_date[:10],
        deleted=deleted,
    )
    return {
        "ok": True,
        "dry_run": False,
        "symbol": symbol,
        "from_date": body.from_date[:10],
        "to_date": body.to_date[:10],
        "match_count": match_count,
        "deleted": deleted,
        "message": f"Deleted {deleted:,} ticks for {symbol} ({body.from_date[:10]} → {body.to_date[:10]} UTC).",
        **storage,
    }


@router.get("/data-info")
async def data_info(db: AsyncSession = Depends(get_db)) -> dict:
    """Check how many ticks are available for training (live DB + staged parquet)."""
    from app.ml.history_manifest import count_parquet_ticks, parquet_epoch_span

    coverage = await tick_coverage_stats(db)
    storage = await _ticks_storage_bytes(db)
    symbols_raw = list(coverage.get("symbols") or [])

    # Non-overlapping sample spacing is clamped to ≥ contract duration in trainer.
    duration = int(settings.contract_duration_seconds)
    interval = max(int(settings.train_sampling_interval_seconds), duration)
    min_labels = 100
    min_span_seconds = min_labels * interval + 600 + duration

    def _normalize_gap_flags(raw: Any) -> dict:
        """tick_coverage_stats returns an int gap count; parquet returns a dict."""
        if isinstance(raw, dict):
            return raw
        try:
            n = int(raw or 0)
        except (TypeError, ValueError):
            return {}
        return {"gap_ticks": n, "has_large_gaps": n > 100}

    # Merge staged parquet sources (often much longer than live Postgres).
    # Use metadata span only — never load 6.5M rows for a status poll.
    hist_sources: dict[str, dict] = {}
    seen_symbols = {str(s["symbol"]) for s in symbols_raw}
    for sym in list(seen_symbols) or ["R_100"]:
        pq_n = count_parquet_ticks(sym)
        if pq_n <= 0:
            continue
        info: dict[str, Any] = {
            "symbol": sym,
            "parquet_ticks": pq_n,
            "tick_count": pq_n,
            "source": "parquet",
            "ready_to_train": pq_n >= 1000,
        }
        try:
            span = parquet_epoch_span(sym)
            if span:
                span_s = int(span["span_seconds"])
                e0 = int(span["oldest_epoch"])
                e1 = int(span["newest_epoch"])
                est_labels = max(0, int((span_s - 600 - duration) // interval)) if span_s > 0 else 0
                info.update(
                    {
                        "tick_count": int(span["tick_count"]),
                        "oldest": datetime.fromtimestamp(e0, tz=timezone.utc).isoformat(),
                        "newest": datetime.fromtimestamp(e1, tz=timezone.utc).isoformat(),
                        "span_hours": round(span_s / 3600.0, 2),
                        "span_days": round(span_s / 86400.0, 2),
                        "est_labels": est_labels,
                        "usable_windows": est_labels,
                        "gap_flags": {},
                        "ready_to_train": est_labels >= min_labels and int(span["tick_count"]) >= 1000,
                    }
                )
        except Exception as exc:
            logger.warning("data_info_parquet_scan_failed", symbol=sym, error=str(exc))
        hist_sources[sym] = info

    if not symbols_raw and not hist_sources:
        return {
            "has_data": False,
            "message": (
                "No tick data collected yet. Confirm Setup so the live worker "
                "starts saving ticks, or Stage SQLite / Download history, then train."
            ),
            "symbols": [],
            "history_sources": [],
            **storage,
        }

    symbols = []
    for s in symbols_raw:
        span_s = float(s.get("span_days") or 0) * 86400.0
        est_labels = max(0, int((span_s - 600 - duration) // interval)) if span_s > 0 else 0
        gap_flags = _normalize_gap_flags(s.get("gap_flags"))
        # Only block readiness on extreme live-table gap counts (not parquet).
        gap_blocked = bool(gap_flags.get("has_large_gaps"))
        usable = 0 if gap_blocked else est_labels
        ready = (not gap_blocked) and usable >= min_labels and int(s["tick_count"]) >= 1000
        hist = hist_sources.get(str(s["symbol"]))
        tick_count = int(s["tick_count"])
        oldest = s.get("oldest")
        newest = s.get("newest")
        span_hours = round(span_s / 3600.0, 2)
        span_days = s.get("span_days")
        # Prefer longer staged history for readiness + UI counters.
        if hist and int(hist.get("tick_count") or hist.get("parquet_ticks") or 0) > tick_count:
            ready = bool(hist.get("ready_to_train"))
            usable = int(hist.get("usable_windows") or hist.get("est_labels") or 0)
            est_labels = int(hist.get("est_labels") or est_labels)
            tick_count = int(hist.get("tick_count") or hist.get("parquet_ticks") or tick_count)
            oldest = hist.get("oldest") or oldest
            newest = hist.get("newest") or newest
            span_hours = float(hist.get("span_hours") or span_hours)
            span_days = hist.get("span_days") if hist.get("span_days") is not None else span_days
            gap_flags = _normalize_gap_flags(hist.get("gap_flags"))
        symbols.append(
            {
                "symbol": s["symbol"],
                "tick_count": tick_count,
                "oldest": oldest,
                "newest": newest,
                "ready_to_train": ready,
                "est_labels": est_labels,
                "usable_windows": usable,
                "min_labels_required": min_labels,
                "est_ticks_per_day": s.get("est_ticks_per_day"),
                "span_days": span_days,
                "span_hours": span_hours,
                "min_span_hours": round(min_span_seconds / 3600.0, 1),
                "gap_flags": gap_flags,
                "staged_parquet_ticks": (hist or {}).get("parquet_ticks")
                or (hist or {}).get("tick_count"),
                "history_source": "parquet" if hist and tick_count > int(s["tick_count"]) else "database",
            }
        )

    # Symbols only present in parquet
    for sym, hist in hist_sources.items():
        if any(s["symbol"] == sym for s in symbols):
            continue
        symbols.append(
            {
                "symbol": sym,
                "tick_count": int(hist.get("tick_count") or hist.get("parquet_ticks") or 0),
                "oldest": hist.get("oldest"),
                "newest": hist.get("newest"),
                "ready_to_train": bool(hist.get("ready_to_train")),
                "est_labels": int(hist.get("est_labels") or 0),
                "usable_windows": int(hist.get("usable_windows") or hist.get("est_labels") or 0),
                "min_labels_required": min_labels,
                "span_days": hist.get("span_days"),
                "span_hours": hist.get("span_hours"),
                "min_span_hours": round(min_span_seconds / 3600.0, 1),
                "gap_flags": hist.get("gap_flags"),
                "staged_parquet_ticks": hist.get("parquet_ticks") or hist.get("tick_count"),
                "source": "parquet",
            }
        )

    total_ticks = sum(int(s.get("tick_count") or 0) for s in symbols)
    max_span_h = max((s.get("span_hours") or 0) for s in symbols) if symbols else 0.0
    if max_span_h >= 72:
        rec_cal, rec_edge, rec_label = 50, 30, "3+ days — stricter (recommended)"
    elif max_span_h >= 48:
        rec_cal, rec_edge, rec_label = 40, 25, "2+ days — medium-strict"
    elif max_span_h >= 24:
        rec_cal, rec_edge, rec_label = 30, 20, "1+ day — balanced"
    else:
        rec_cal, rec_edge, rec_label = 20, 15, "~1 day — starter (easier to finish)"

    prefs = load_train_prefs()
    return {
        "has_data": total_ticks > 0,
        "total_ticks": total_ticks,
        **storage,
        "min_needed": 1000,
        "min_labels_required": min_labels,
        "min_span_hours": round(min_span_seconds / 3600.0, 1),
        "train_sampling_interval_seconds": interval,
        "ready": any(s["ready_to_train"] for s in symbols),
        "symbols": symbols,
        "history_sources": list(hist_sources.values()),
        "train_max_ticks": settings.train_max_ticks,
        "train_defaults": {
            "min_calibration_samples": settings.train_min_calibration_samples,
            "min_edge_selected": settings.train_edge_min_selected,
        },
        "train_prefs": prefs,
        "train_recommended": {
            "min_calibration_samples": rec_cal,
            "min_edge_selected": rec_edge,
            "label": rec_label,
            "span_hours": max_span_h,
        },
        "env_is_default_only": True,
        "note": (
            f"Training needs ≥{min_labels} non-overlapping usable windows "
            f"(one every {interval}s) ≈{min_span_seconds/3600.0:.1f}h continuous coverage. "
            "Staged parquet/SQLite history is preferred when longer than the live table. "
            "After deploy, change calibration/edge only in this UI — not in .env."
        ),
    }


@router.get("/health")
async def train_health(db: AsyncSession = Depends(get_db)) -> dict:
    """Tick coverage + active model registry summary."""
    from app.models.model_version import ModelVersion

    coverage = await tick_coverage_stats(db)
    active = (
        await db.execute(
            select(ModelVersion).where(ModelVersion.is_active.is_(True))
        )
    ).scalars().all()
    return {
        "coverage": coverage,
        "active_models": [
            {
                "id": m.id,
                "direction": m.direction,
                "symbol": m.symbol,
                "version_tag": m.version_tag,
                "algorithm": m.algorithm,
                "has_demonstrated_edge": m.has_demonstrated_edge,
                "alerts_paused": m.alerts_paused,
                "auc_roc": m.auc_roc,
                "brier_score": m.brier_score,
            }
            for m in active
        ],
    }


async def _run_training_background(
    symbol: str,
    barrier_distance: float,
    barrier_direction: str,
    duration_seconds: int,
    min_calibration_samples: int | None = None,
    min_edge_selected: int | None = None,
):
    """Background coroutine that runs the training pipeline."""
    global _training_state

    # Support training both directions in one job
    directions = (
        ["upper", "lower"]
        if str(barrier_direction).lower() == "both"
        else [barrier_direction]
    )
    if len(directions) > 1:
        results = []
        for d in directions:
            _training_state = {
                "status": "running",
                "progress": f"Training {d} model...",
                "result": None,
                "error": None,
            }
            await _run_training_background(
                symbol,
                barrier_distance,
                d,
                duration_seconds,
                min_calibration_samples=min_calibration_samples,
                min_edge_selected=min_edge_selected,
            )
            if _training_state.get("status") == "error":
                return
            results.append({"direction": d, "result": _training_state.get("result")})
        _training_state = {
            "status": "done",
            "progress": "Trained upper + lower models.",
            "result": {"directions": results, **(results[-1].get("result") or {})},
            "error": None,
        }
        return

    _training_state = {"status": "running", "progress": "Loading tick data...", "result": None, "error": None}

    try:
        max_ticks = int(settings.train_max_ticks or 0)
        async with async_session() as session:
            df = await load_ticks_dataframe(
                session,
                symbol=symbol,
                max_ticks=max_ticks,
            )
            df, history_source = prefer_longer_history(df, symbol)
            _training_state["progress"] = f"Using {history_source} history ({len(df):,} ticks)..."
            # Load quotes for economic backtest join
            qres = await session.execute(
                select(Quote)
                .where(Quote.symbol == symbol)
                .where(Quote.barrier_direction == barrier_direction)
                .order_by(Quote.id.asc())
            )
            quotes = qres.scalars().all()
            quotes_df = pd.DataFrame(
                [
                    {
                        "quote_epoch": q.quote_epoch or q.spot_time,
                        "spot_time": q.spot_time,
                        "barrier_direction": q.barrier_direction,
                        "contract_type": q.contract_type,
                        "barrier_input": q.barrier_input,
                        "duration_value": q.duration_value,
                        "duration_unit": q.duration_unit,
                        "currency": q.currency,
                        "ask_price": float(q.ask_price),
                        "payout": float(q.payout),
                        "breakeven_prob": float(q.breakeven_prob) if q.breakeven_prob is not None else None,
                    }
                    for q in quotes
                    if (q.quote_epoch or q.spot_time) and q.ask_price and q.payout
                ]
            )

        if len(df) < 1000:
            _training_state = {
                "status": "error",
                "progress": "",
                "result": None,
                "error": f"Not enough ticks: {len(df)} collected, need at least 1000.",
            }
            return

        _training_state["progress"] = (
            f"Loaded {len(df):,} ticks (+{len(quotes_df):,} quotes) — building features & labels..."
        )

        loop = asyncio.get_event_loop()
        orchestrator = TrainingOrchestrator(
            barrier_distance=barrier_distance,
            barrier_direction=barrier_direction,
            duration_seconds=duration_seconds,
            include_xgboost=True,
            symbol=symbol,
        )

        def _on_train_progress(msg: str) -> None:
            global _training_state
            _training_state = {
                **_training_state,
                "status": "running",
                "progress": str(msg),
            }

        _training_state["progress"] = (
            "Training with non-overlapping samples + walk-forward metadata "
            "(XGBoost/CatBoost/LSTM/ensembles)…"
        )

        results = await loop.run_in_executor(
            None,
            lambda: orchestrator.train_full_pipeline(
                df,
                sampling_interval_seconds=settings.train_sampling_interval_seconds,
                quotes_df=quotes_df,
                min_calibration_samples=min_calibration_samples,
                min_edge_selected=min_edge_selected,
                on_progress=_on_train_progress,
            ),
        )

        if "error" in results:
            _training_state = {
                "status": "error",
                "progress": "",
                "result": None,
                "error": results["error"],
            }
        else:
            # Persist / activate model version in DB
            meta = results.get("metadata") or {}
            try:
                async with async_session() as session:
                    mv = await save_model_version(session, meta, results, activate=False)
                    results["model_version_id"] = mv.id
                    results["is_active"] = False
                    results["promotion_required"] = True
            except Exception as reg_err:
                logger.warning("model_registry_persist_failed", error=str(reg_err))

            def _slim(block):
                if not isinstance(block, dict):
                    return {}
                return {
                    k: v for k, v in block.items()
                    if k in (
                        "auc_roc", "brier_score", "accuracy", "log_loss_val",
                        "n_samples", "selected_signal_count",
                        "selected_win_rate", "selected_win_rate_ci_lower",
                    )
                }

            selected = results.get("selected_pipeline")
            _training_state = {
                "status": "done",
                "progress": "Training complete!",
                "result": {
                    "timestamp": results.get("timestamp"),
                    "split_info": results.get("split_info"),
                    "selected_pipeline": selected,
                    "barrier_unit": results.get("barrier_unit"),
                    "contract_semantics": results.get("contract_semantics"),
                    "validation_selection": results.get("validation_selection"),
                    "selection_note": results.get("selection_note"),
                    "candidate_probability_metrics": {
                        name: {
                            key: value
                            for key, value in metrics.items()
                            if key in (
                                "raw_brier", "calibrated_brier", "raw_auc",
                                "calibrated_auc", "calibration_method", "n_samples",
                            )
                        }
                        for name, metrics in (results.get("candidate_probability_metrics") or {}).items()
                        if isinstance(metrics, dict)
                    },
                    "baseline": _slim(results.get("baseline")),
                    "logistic_regression": _slim(results.get("logistic_regression")),
                    "volatility_distance": _slim(results.get("volatility_distance")),
                    "xgboost": _slim(results.get("xgboost")),
                    "catboost": _slim(results.get("catboost")),
                    "lstm": _slim(results.get("lstm")),
                    "weighted": _slim(results.get("weighted")),
                    "stacking": _slim(results.get("stacking")),
                    "selected": _slim(results.get("selected")),
                    "historical_frequency": results.get("historical_frequency"),
                    "model_paths": results.get("model_paths"),
                    "top_features": (results.get("top_features") or [])[:10],
                    "has_demonstrated_edge": results.get("has_demonstrated_edge"),
                    "edge_description": results.get("edge_description"),
                    "quote_backtest_note": results.get("quote_backtest_note"),
                    "quote_match_rate": results.get("quote_match_rate"),
                    "version_tag": results.get("version_tag"),
                    "model_version_id": results.get("model_version_id"),
                    "walk_forward_folds": (results.get("metadata") or {}).get("walk_forward_folds"),
                    "sampling_interval_seconds": (results.get("metadata") or {}).get(
                        "sampling_interval_seconds"
                    ),
                },
                "error": None,
            }
            logger.info("training_api_complete", selected=selected, result_keys=list(results.keys()))

    except Exception as exc:
        logger.error("training_api_failed", error=str(exc))
        _training_state = {
            "status": "error",
            "progress": "",
            "result": None,
            "error": str(exc),
        }


@router.post("/start")
async def start_training(
    background_tasks: BackgroundTasks,
    symbol: str = "R_100",
    barrier_distance: float = 0.09,
    barrier_direction: str = "upper",
    duration_seconds: int = 540,
    min_calibration_samples: int | None = None,
    min_edge_selected: int | None = None,
    auto_raise: bool | None = None,
    task: str = "touch",
) -> dict:
    """
    Kick off the ML training pipeline in the background.
    Poll GET /train/status for progress and results.

    UI values override saved prefs; saved prefs override .env defaults.
    After deploy, change strictness in the UI — do not edit .env for this.
    """
    if _training_state["status"] == "running":
        raise HTTPException(status_code=409, detail="Training already in progress.")

    prefs = load_train_prefs()
    cal = (
        int(min_calibration_samples)
        if min_calibration_samples is not None
        else int(prefs["min_calibration_samples"])
    )
    edge = (
        int(min_edge_selected)
        if min_edge_selected is not None
        else int(prefs["min_edge_selected"])
    )
    if not (11 <= cal <= 200):
        raise HTTPException(status_code=400, detail="min_calibration_samples must be 11–200")
    if not (5 <= edge <= 200):
        raise HTTPException(status_code=400, detail="min_edge_selected must be 5–200")

    save_train_prefs(
        {
            "min_calibration_samples": cal,
            "min_edge_selected": edge,
            "auto_raise": prefs["auto_raise"] if auto_raise is None else bool(auto_raise),
        }
    )

    task_id = str(task or "touch").strip().lower()
    if task_id in {"direction_5m", "direction_15m", "direction_30m"}:
        asyncio.create_task(_run_direction_background(symbol, task_id))
        return {
            "status": "started",
            "message": f"Training {task_id}. The model stays inactive until it passes the gates and is promoted.",
            "task": task_id,
        }
    if task_id not in {"touch", "touch_upper", "touch_lower"}:
        raise HTTPException(status_code=400, detail="task must be touch or direction_5m, direction_15m, direction_30m")

    asyncio.create_task(
        _run_training_background(
            symbol,
            barrier_distance,
            barrier_direction,
            duration_seconds,
            min_calibration_samples=cal,
            min_edge_selected=edge,
        )
    )

    return {
        "status": "started",
        "message": "Training job queued. It will not become active until you promote a model that passed the gates.",
        "min_calibration_samples": cal,
        "min_edge_selected": edge,
        "env_is_default_only": True,
        "task": "touch",
    }


async def _run_direction_background(symbol: str, task_id: str) -> None:
    """Train one directional horizon and save it inactive."""
    global _training_state
    _training_state = {
        "status": "running",
        "progress": f"Training {task_id} from retained history...",
        "result": None,
        "error": None,
    }
    try:
        max_ticks = int(settings.train_max_ticks or 0)
        async with async_session() as session:
            df = await load_ticks_dataframe(session, symbol=symbol, max_ticks=max_ticks)
        df, history_source = prefer_longer_history(df, symbol)
        from app.ml.direction_task import train_direction_task

        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(
            None, lambda: train_direction_task(df, task_id, symbol=symbol)
        )
        if results.get("error"):
            _training_state = {"status": "error", "progress": "", "result": None, "error": results["error"]}
            return
        meta = results["metadata"]
        async with async_session() as session:
            await save_model_version(session, meta, results, activate=False)
        _training_state = {
            "status": "done",
            "progress": "Direction model saved. Promote it only after the gates pass.",
            "result": {
                "version_tag": meta["version_tag"],
                "selected_pipeline": meta["selected_pipeline"],
                "promotion_ready": bool(meta["promotion_ready"]),
                "has_demonstrated_edge": bool(meta["promotion_ready"]),
                "is_active": False,
                "task_id": task_id,
                "history_source": history_source,
                "metrics": meta.get("metrics") or {},
            },
            "error": None,
        }
    except Exception as exc:
        logger.error("direction_training_failed", error=str(exc))
        _training_state = {"status": "error", "progress": "", "result": None, "error": str(exc)}


# ── Download endpoint (no WebSocket needed) ────────────────────────────────────

_download_state: dict = {"status": "idle", "progress": "", "ticks_downloaded": 0, "error": None}


@router.get("/download-status")
async def download_status() -> dict:
    """Get current historical data download status."""
    return _download_state


async def _run_download_background(
    symbol: str,
    days_back: int,
    app_id: str,
    target_ticks: int | None = None,
):
    """Background task: download historical ticks via public WS paging."""
    global _download_state
    goal = f"{target_ticks:,} ticks" if target_ticks else f"{days_back} days"
    _download_state = {
        "status": "running",
        "progress": f"Fetching up to {goal} of {symbol}…",
        "ticks_downloaded": 0,
        "error": None,
        "target_ticks": target_ticks,
    }

    async def on_progress(msg: str):
        _download_state["progress"] = msg
        for key in ("total_new=", "total="):
            if key in msg:
                try:
                    part = msg.split(key, 1)[1]
                    num = []
                    for ch in part:
                        if ch.isdigit() or ch == ",":
                            num.append(ch)
                        else:
                            break
                    _download_state["ticks_downloaded"] = int("".join(num).replace(",", ""))
                except Exception:
                    pass
                break

    try:
        total = await download_and_store_ticks(
            symbol=symbol,
            days_back=days_back,
            app_id=app_id,
            on_progress=on_progress,
            target_ticks=target_ticks,
        )
        _download_state = {
            "status": "done",
            "progress": f"Saved {total:,} new ticks.",
            "ticks_downloaded": total,
            "error": None,
            "target_ticks": target_ticks,
        }
    except Exception as e:
        logger.error("download_background_failed", error=str(e))
        _download_state = {
            "status": "error",
            "progress": "",
            "ticks_downloaded": 0,
            "error": str(e),
            "target_ticks": target_ticks,
        }


@router.get("/download-options")
async def download_options(symbol: str = "R_100") -> dict:
    """Tick-size picker metadata for the Train UI."""
    from app.collector.http_history import estimate_ticks_per_day

    per_day = estimate_ticks_per_day(symbol)
    presets = [
        {
            "ticks": 5_000,
            "label": f"5,000 ticks — minimum to start training (~{(5000/per_day)*24:.1f}h)",
            "need": "minimum",
        },
        {
            "ticks": 10_000,
            "label": f"10,000 ticks — light (~{(10000/per_day)*24:.1f}h)",
            "need": "light",
        },
        {
            "ticks": 20_000,
            "label": f"20,000 ticks — medium (~{(20000/per_day)*24:.1f}h)",
            "need": "medium",
        },
        {
            "ticks": 30_000,
            "label": f"30,000 ticks — solid (~{(30000/per_day)*24:.1f}h)",
            "need": "solid",
        },
        {
            "ticks": 45_000,
            "label": f"45,000 ticks — about 1 full day (≈{per_day:,}/day)",
            "need": "1_day",
        },
        {
            "ticks": 60_000,
            "label": "60,000 ticks — more than 1 day (API may stop ~24h)",
            "need": "heavy",
        },
        {
            "ticks": 90_000,
            "label": "90,000 ticks — about 2 days (API may stop ~24h)",
            "need": "2_days",
        },
        {
            "ticks": 0,
            "label": f"ALL available from API (max ~{per_day:,} ticks / ~24h public limit)",
            "need": "all",
        },
    ]
    return {
        "symbol": symbol,
        "ticks_per_day": per_day,
        "public_api_note": (
            "Pick how many ticks you need. "
            "Public Deriv history is usually only the last ~24 hours. "
            "ALL = download everything the API still has."
        ),
        "presets": presets,
    }


_sqlite_stage_state: dict = {
    "status": "idle",
    "progress": "",
    "error": None,
    "result": None,
}


@router.get("/stage-sqlite-status")
async def stage_sqlite_status() -> dict:
    return dict(_sqlite_stage_state)


@router.post("/stage-sqlite")
async def stage_sqlite(
    symbol: str = Query(default="R_100", min_length=1, max_length=32),
    path: str | None = Query(default=None),
) -> dict:
    """Copy market.sqlite ticks into parquet in the background (does not block the chart)."""
    from app.ml.history_manifest import count_parquet_ticks
    from app.ml.sqlite_ticks import (
        count_sqlite_ticks,
        default_sqlite_path,
        export_sqlite_to_parquet,
        load_sqlite_ticks,
    )

    if _sqlite_stage_state["status"] == "running":
        raise HTTPException(status_code=409, detail="SQLite staging already running.")

    source = Path(path) if path else default_sqlite_path()
    if source is None or not Path(source).is_file():
        raise HTTPException(
            status_code=404,
            detail=(
                "market.sqlite not found. Put it at data/market.sqlite "
                "(or candel/market.sqlite with the candel volume mounted)."
            ),
        )

    sqlite_n = count_sqlite_ticks(source, symbol=symbol)
    parquet_n = count_parquet_ticks(symbol)
    if parquet_n >= sqlite_n and sqlite_n > 0:
        return {
            "ok": True,
            "already_staged": True,
            "source": str(source),
            "symbol": symbol,
            "tick_count": int(parquet_n),
            "message": (
                f"Parquet already has {parquet_n:,} ticks "
                f"(≥ SQLite {sqlite_n:,}). No reload needed — press Train."
            ),
        }

    _sqlite_stage_state.update(
        {
            "status": "running",
            "progress": f"Loading {sqlite_n:,} ticks from SQLite…",
            "error": None,
            "result": None,
        }
    )

    async def _run() -> None:
        try:
            frame = await asyncio.to_thread(load_sqlite_ticks, source, symbol)
            dest = await asyncio.to_thread(
                export_sqlite_to_parquet,
                source,
                settings.parquet_data_dir,
                symbol,
                frame,
            )
            span_hours = (int(frame["epoch"].iloc[-1]) - int(frame["epoch"].iloc[0])) / 3600.0
            result = {
                "ok": True,
                "source": str(source),
                "parquet": str(dest),
                "symbol": symbol,
                "tick_count": int(len(frame)),
                "start_utc": frame["tick_time"].iloc[0].isoformat(),
                "end_utc": frame["tick_time"].iloc[-1].isoformat(),
                "span_hours": round(span_hours, 2),
                "message": (
                    f"Staged {len(frame):,} ticks into parquet. "
                    "Train will prefer this file when it is longer than the database."
                ),
            }
            _sqlite_stage_state.update(
                {"status": "done", "progress": result["message"], "result": result, "error": None}
            )
        except Exception as exc:
            _sqlite_stage_state.update(
                {"status": "error", "progress": "", "error": str(exc), "result": None}
            )

    asyncio.create_task(_run())
    return {
        "ok": True,
        "started": True,
        "symbol": symbol,
        "source": str(source),
        "sqlite_tick_count": sqlite_n,
        "message": (
            f"Staging {sqlite_n:,} ticks in the background. "
            "Chart stays live — poll /train/stage-sqlite-status or wait, then Train."
        ),
    }


@router.get("/export-ticks")
async def export_ticks(
    symbol: str = Query(default="R_100", min_length=1, max_length=32),
    format: str = Query(default="csv", pattern="^(csv|parquet)$"),
    db: AsyncSession = Depends(get_db),
):
    """Download every stored tick for one symbol to the browser (local file).

    This exports the database, not a fresh Deriv pull. Use Fill history first
    if you need the public ~24h window filled into the DB.
    """
    result = await db.execute(
        select(Tick.symbol, Tick.epoch, Tick.quote, Tick.tick_time, Tick.is_gap)
        .where(Tick.symbol == symbol)
        .order_by(Tick.epoch.asc())
    )
    rows = result.all()
    if not rows:
        raise HTTPException(status_code=404, detail=f"No ticks stored for {symbol}.")

    frame = pd.DataFrame(
        [
            {
                "symbol": str(symbol_value),
                "epoch": int(epoch),
                "quote": float(quote),
                "tick_time": tick_time.isoformat() if tick_time is not None else "",
                "is_gap": int(is_gap or 0),
            }
            for symbol_value, epoch, quote, tick_time, is_gap in rows
        ]
    )
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    if format == "parquet":
        import io

        buffer = io.BytesIO()
        frame.to_parquet(buffer, index=False)
        buffer.seek(0)
        # Also keep a copy under the server parquet folder when configured.
        try:
            root = settings.parquet_data_dir
            if root:
                os.makedirs(root, exist_ok=True)
                server_path = os.path.join(root, f"{symbol}_export_{stamp}.parquet")
                frame.to_parquet(server_path, index=False)
        except Exception as exc:
            logger.warning("export_ticks_server_copy_failed", error=str(exc))
        return Response(
            content=buffer.getvalue(),
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="{symbol}_ticks_{stamp}.parquet"',
                "X-Tick-Count": str(len(frame)),
            },
        )

    def _csv_lines():
        yield "symbol,epoch,quote,tick_time,is_gap\n"
        for row in frame.itertuples(index=False):
            yield f"{row.symbol},{row.epoch},{row.quote},{row.tick_time},{row.is_gap}\n"

    return StreamingResponse(
        _csv_lines(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{symbol}_ticks_{stamp}.csv"',
            "X-Tick-Count": str(len(frame)),
        },
    )


@router.post("/download")
async def start_download(
    background_tasks: BackgroundTasks,
    symbol: str = "R_100",
    days_back: int = 1,
    target_ticks: int | None = None,
    app_id: str = "36544",
) -> dict:
    """
    Download historical ticks. Prefer target_ticks (user-picked size).
    Poll GET /train/download-status for progress.
    """
    if _download_state["status"] == "running":
        raise HTTPException(status_code=409, detail="Download already in progress.")
    if _training_state["status"] == "running":
        raise HTTPException(status_code=409, detail="Training is running. Wait for it to finish.")

    # 0 / None = download all available in the lookback window (API floor still applies)
    if target_ticks is not None and target_ticks < 0:
        raise HTTPException(status_code=400, detail="target_ticks cannot be negative")
    if target_ticks is not None and 0 < target_ticks < 1000:
        raise HTTPException(status_code=400, detail="target_ticks must be at least 1000 (or 0 for ALL)")

    effective_target = None if not target_ticks else target_ticks
    goal = "ALL available ticks" if effective_target is None else f"{effective_target:,} ticks"
    _download_state["status"] = "running"
    _download_state["progress"] = f"Queued download: {goal} of {symbol}…"
    _download_state["ticks_downloaded"] = 0
    _download_state["error"] = None
    _download_state["target_ticks"] = effective_target

    asyncio.create_task(
        _run_download_background(symbol, days_back, app_id, target_ticks=effective_target)
    )

    return {
        "status": "started",
        "message": f"Downloading up to {goal} for {symbol}. Poll /train/download-status.",
        "target_ticks": effective_target,
    }


class PromoteBody(BaseModel):
    version_tag: str = Field(min_length=3, max_length=64)


@router.post("/promote")
async def promote_trained_model(
    body: PromoteBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Activate a saved model only after the recorded promotion gates pass."""
    from app.ml.registry import promote_model_version

    try:
        row = await promote_model_version(db, body.version_tag)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "status": "promoted",
        "version_tag": row.version_tag,
        "direction": row.direction,
        "is_active": row.is_active,
    }
