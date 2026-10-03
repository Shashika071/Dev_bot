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
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, async_session
from app.models.tick import Tick
from app.ml.trainer import TrainingOrchestrator
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
    result = await db.execute(
        select(
            Tick.symbol,
            func.count(Tick.id).label("tick_count"),
            func.min(Tick.tick_time).label("oldest"),
            func.max(Tick.tick_time).label("newest"),
        ).group_by(Tick.symbol)
    )
    rows = result.all()

    if not rows:
        return {
            "has_data": False,
            "message": "No tick data collected yet. The worker must be connected to Deriv to collect ticks.",
            "symbols": [],
        }

    symbols = [
        {
            "symbol": r.symbol,
            "tick_count": r.tick_count,
            "oldest": str(r.oldest) if r.oldest else None,
            "newest": str(r.newest) if r.newest else None,
            "ready_to_train": r.tick_count >= 5000,
        }
        for r in rows
    ]

    total_ticks = sum(s["tick_count"] for s in symbols)
    return {
        "has_data": total_ticks > 0,
        "total_ticks": total_ticks,
        "min_needed": 5000,
        "ready": any(s["ready_to_train"] for s in symbols),
        "symbols": symbols,
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
        # Load ticks from the database
        async with async_session() as session:
            result = await session.execute(
                select(Tick)
                .where(Tick.symbol == symbol)
                .order_by(Tick.epoch.asc())
            )
            ticks = result.scalars().all()

        if len(ticks) < 1000:
            _training_state = {
                "status": "error",
                "progress": "",
                "result": None,
                "error": f"Not enough ticks: {len(ticks)} collected, need at least 1000.",
            }
            return

        _training_state["progress"] = f"Loaded {len(ticks):,} ticks — building features & labels..."

        df = pd.DataFrame(
            [{"epoch": t.epoch, "tick_time": t.tick_time, "quote": float(t.quote)} for t in ticks]
        )

        # Run the training pipeline in a thread pool to avoid blocking the event loop
        loop = asyncio.get_event_loop()
        orchestrator = TrainingOrchestrator(
            barrier_distance=barrier_distance,
            barrier_direction=barrier_direction,
            duration_seconds=duration_seconds,
            include_xgboost=True,
            symbol=symbol,
        )

        _training_state["progress"] = (
            "Training XGBoost + CatBoost + LSTM and comparing ensembles (several minutes)..."
        )

        results = await loop.run_in_executor(
            None,
            orchestrator.train_full_pipeline,
            df,
            60,  # sample decision points every 60s
        )

        if "error" in results:
            _training_state = {
                "status": "error",
                "progress": "",
                "result": None,
                "error": results["error"],
            }
        else:
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
                    "version_tag": results.get("version_tag"),
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
