"""Mutually exclusive background jobs for the synthetic strategy."""
from __future__ import annotations

import asyncio
import copy

from app.config import settings
from app.synthetic.history import DEFAULT_DAYS, download_history, history_status, validate_days
from app.synthetic.ml import train_model


class SyntheticJobs:
    def __init__(self) -> None:
        self.history_job = {"status": "idle", "progress": "", "error": None}
        self.train_job = {"status": "idle", "progress": "", "error": None, "result": None}
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
            return self.snapshot()

    async def start_train(self) -> dict:
        async with self._lock:
            self._check_busy()
            if not history_status(settings.model_dir)["ready_for_train"]:
                raise ValueError("Download at least 60 days of history before training")
            self.train_job = {"status": "running", "progress": "Starting CatBoost/control validation", "error": None, "result": None}

            async def run() -> None:
                try:
                    result = await asyncio.to_thread(train_model, settings.model_dir, progress=self.train_job)
                    self.train_job["result"] = result
                except Exception as exc:
                    self.train_job.update(status="error", error=str(exc), progress="Training failed")

            self._train_task = asyncio.create_task(run())
            return self.snapshot()


synthetic_jobs = SyntheticJobs()
