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
        self._lock = asyncio.Lock()

    def snapshot(self) -> dict:
        return {
            "history": history_status(settings.model_dir, copy.deepcopy(self.history_job)),
            "train": copy.deepcopy(self.train_job),
        }

    def _check_busy(self) -> None:
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


synthetic_jobs = SyntheticJobs()
