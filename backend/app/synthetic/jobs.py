"""Mutually exclusive background jobs for the synthetic strategy."""
from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path

from app.config import settings
from app.synthetic.history import DEFAULT_DAYS, download_history, history_status, validate_days
from app.synthetic.ml import publish_train_job


def load_saved_train_job(model_base: str | Path) -> dict:
    """Restore the last train stage. A leftover 'running' job means the process died."""
    default = {"status": "idle", "progress": "", "error": None, "result": None}
    path = Path(model_base) / "synthetic_r100" / "train_job.json"
    if not path.is_file():
        return dict(default)
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
        return dict(default)
    if not isinstance(saved, dict):
        return dict(default)
    job = {
        **default,
        "status": saved.get("status") if isinstance(saved.get("status"), str) else "idle",
        "progress": saved.get("progress") if isinstance(saved.get("progress"), str) else "",
        "error": saved.get("error") if isinstance(saved.get("error"), str) else None,
        "started_at": saved.get("started_at"),
        "finished_at": saved.get("finished_at"),
    }
    if job["status"] == "running":
        job.update(
            status="error", progress="Training failed",
            error="Training stopped because the backend restarted before it finished. No model was saved.",
        )
        publish_train_job(model_base, job)
    return job


class SyntheticJobs:
    def __init__(self) -> None:
        self.history_job = {"status": "idle", "progress": "", "error": None}
        self.train_job = load_saved_train_job(settings.model_dir)
        self._history_task: asyncio.Task | None = None
        self._train_task: asyncio.Task | None = None
        self._evidence_task: asyncio.Task | None = None
        self.evidence_job = {"status": "idle", "progress": "", "error": None}
        self._lock = asyncio.Lock()

    def snapshot(self) -> dict:
        return {
            "history": history_status(settings.model_dir, copy.deepcopy(self.history_job)),
            "train": copy.deepcopy(self.train_job),
            "evidence": copy.deepcopy(self.evidence_job),
        }

    def _check_busy(self) -> None:
        if self._evidence_task is not None and not self._evidence_task.done():
            raise ValueError("Signal evidence job is already running")
        if self._history_task is not None and not self._history_task.done():
            raise ValueError("Synthetic history download is already running")
        if self._train_task is not None and not self._train_task.done():
            raise ValueError("Synthetic model training is already running")

    async def start_history(self, *, days: int = DEFAULT_DAYS) -> dict:
        days = validate_days(days)
        async with self._lock:
            self._check_busy()
            self.history_job = {"status": "running", "progress": "Starting public history download", "error": None}

            async def run() -> None:
                try:
                    await download_history(settings.model_dir, days=days, progress=self.history_job)
                except Exception as exc:
                    self.history_job.update(status="error", error=str(exc))

            self._history_task = asyncio.create_task(run())
            return await asyncio.to_thread(self.snapshot)

    async def start_train(self) -> dict:
        raise ValueError("The previous CatBoost model was removed. Use the research signal engine.")

    async def start_evidence(self, *, repair: bool = False, payout: float | None = None) -> dict:
        from app.trading_ml.config import validate_payout
        from app.trading_ml.service import research_status
        payout = validate_payout(payout)
        async with self._lock:
            self._check_busy()
            if research_status(settings.model_dir).get("job", {}).get("status") == "running":
                raise ValueError("Wait for the research job to finish")
            self.evidence_job = {"status": "running", "progress": "Recovering missing candles" if repair else "Comparing filters at 0, 5, 10 and 20 second entry delays", "error": None}

            async def run():
                try:
                    if repair:
                        from app.synthetic.history import repair_gaps
                        from app.deriv.client import DerivWSClient
                        client = DerivWSClient()
                        try:
                            await client.connect()
                            result = await repair_gaps(settings.model_dir, client)
                        finally:
                            await client.disconnect()
                    else:
                        def replay():
                            import pandas as pd
                            from app.synthetic.history import load_frames
                            from app.synthetic.replay import compare_filters
                            root = Path(settings.model_dir) / "synthetic_r100" / "trading_ml"
                            ticks = []
                            for path in (root / "ticks.parquet", root / "live_ticks.parquet"):
                                if path.exists():
                                    ticks.extend(pd.read_parquet(path).to_dict("records"))
                            return compare_filters(load_frames(settings.model_dir), ticks, net_payout=payout)
                        result = await asyncio.to_thread(replay)
                    target = Path(settings.model_dir) / "synthetic_r100" / ("gap_repair.json" if repair else "filter_comparison.json")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    temporary = target.with_suffix(".tmp")
                    temporary.write_text(json.dumps(result, allow_nan=False, indent=2), encoding="utf-8")
                    temporary.replace(target)
                    self.evidence_job.update(status="done", progress="Gap repair complete" if repair else "Filter comparison complete", result=result)
                except Exception as exc:
                    self.evidence_job.update(status="error", error=f"Evidence job failed ({type(exc).__name__})")
            self._evidence_task = asyncio.create_task(run())
            return copy.deepcopy(self.evidence_job)


synthetic_jobs = SyntheticJobs()
