"""
Training API — triggers the ML model training pipeline via HTTP.
Fetches stored tick data from the database and runs the full training orchestrator.
"""

import asyncio
import os
from typing import Any

import pandas as pd
import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db, async_session
from app.features.tick_loader import load_ticks_dataframe, tick_coverage_stats
from app.ml.registry import save_and_activate_model_version
from app.ml.trainer import TrainingOrchestrator
from app.models.quote import Quote
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


@router.get("/status")
async def training_status() -> dict:
    """Get current training job status."""
    return _training_state


@router.get("/models")
async def list_trained_models() -> dict:
    """
    Return saved per-direction model metadata for the dashboard.
    Reads latest_{direction}.json artefacts from MODEL_DIR.
    """
    import glob
    import json
    from app.config import settings

    model_dir = settings.model_dir
    models = []
    for path in sorted(glob.glob(os.path.join(model_dir, "latest_*.json"))):
        try:
            with open(path, encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            continue
        models.append(
            {
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
                "paths_present": {
                    k: bool(v and os.path.exists(v))
                    for k, v in (meta.get("paths") or {}).items()
                },
            }
        )

    return {
        "trained": len(models) > 0,
        "models": models,
        "min_ticks_to_train": 1000,
        "recommended_ticks": 5000,
    }


@router.get("/data-info")
async def data_info(db: AsyncSession = Depends(get_db)) -> dict:
    """Check how many ticks are available in the database for training."""
    coverage = await tick_coverage_stats(db)
    symbols_raw = coverage.get("symbols") or []
    if not symbols_raw:
        return {
            "has_data": False,
            "message": (
                "No tick data collected yet. Confirm Setup so the live worker "
                "starts saving ticks, then return here to train."
            ),
            "symbols": [],
        }

    # Non-overlapping sample spacing is clamped to ≥ contract duration in trainer.
    duration = int(settings.contract_duration_seconds)
    interval = max(int(settings.train_sampling_interval_seconds), duration)
    min_labels = 100
    # entry_epochs = arange(min+600, max-duration, interval)
    min_span_seconds = min_labels * interval + 600 + duration

    symbols = []
    for s in symbols_raw:
        span_s = float(s.get("span_days") or 0) * 86400.0
        est_labels = max(0, int((span_s - 600 - duration) // interval)) if span_s > 0 else 0
        ready = est_labels >= min_labels and int(s["tick_count"]) >= 1000
        symbols.append(
            {
                "symbol": s["symbol"],
                "tick_count": s["tick_count"],
                "oldest": s.get("oldest"),
                "newest": s.get("newest"),
                "ready_to_train": ready,
                "est_labels": est_labels,
                "min_labels_required": min_labels,
                "est_ticks_per_day": s.get("est_ticks_per_day"),
                "span_days": s.get("span_days"),
                "span_hours": round(span_s / 3600.0, 2),
                "min_span_hours": round(min_span_seconds / 3600.0, 1),
                "gap_flags": s.get("gap_flags"),
            }
        )
    total_ticks = sum(s["tick_count"] for s in symbols)
    return {
        "has_data": total_ticks > 0,
        "total_ticks": total_ticks,
        "min_needed": 1000,
        "min_labels_required": min_labels,
        "min_span_hours": round(min_span_seconds / 3600.0, 1),
        "train_sampling_interval_seconds": interval,
        "ready": any(s["ready_to_train"] for s in symbols),
        "symbols": symbols,
        "train_max_ticks": settings.train_max_ticks,
        "note": (
            f"Training needs ≥{min_labels} non-overlapping labels "
            f"(one every {interval}s) ≈{min_span_seconds/3600.0:.1f}h continuous coverage. "
            "Tick count alone is not enough."
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
            await _run_training_background(symbol, barrier_distance, d, duration_seconds)
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
                    mv = await save_and_activate_model_version(session, meta, results)
                    results["model_version_id"] = mv.id
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
                    "baseline": _slim(results.get("baseline")),
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
) -> dict:
    """
    Kick off the ML training pipeline in the background.
    Poll GET /train/status for progress and results.
    """
    if _training_state["status"] == "running":
        raise HTTPException(status_code=409, detail="Training already in progress.")

    # Schedule the async coroutine on the running event loop
    asyncio.create_task(
        _run_training_background(symbol, barrier_distance, barrier_direction, duration_seconds)
    )

    return {"status": "started", "message": "Training job queued. Poll /train/status for updates."}


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
