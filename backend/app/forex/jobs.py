"""Background history download and train jobs for the Forex page."""
from __future__ import annotations

import asyncio
from typing import Any

from app.config import settings
from app.forex.history import download_history, history_status
from app.forex.ml.train import train_eurusd


class ForexJobs:
    def __init__(self):
        self.history_job: dict[str, Any] = {"status": "idle", "progress": "", "error": None}
        self.train_job: dict[str, Any] = {"status": "idle", "progress": "", "error": None, "result": None}
        self._history_task: asyncio.Task | None = None
        self._train_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    def snapshot(self) -> dict:
        return {
            "history": history_status(settings.model_dir, self.history_job),
            "train": dict(self.train_job),
        }

    def _busy(self, kind: str) -> str | None:
        if self._history_task and not self._history_task.done():
            return "History download already running"
        if self._train_task and not self._train_task.done():
            return "Training already running"
        if kind == "train" and self.history_job.get("status") == "running":
            return "Cannot train while history download is running"
        if kind == "history" and self.train_job.get("status") == "running":
            return "Cannot download history while training is running"
        return None

    async def start_history(self) -> dict:
        async with self._lock:
            busy = self._busy("history")
            if busy:
                snap = self.snapshot()
                snap["history"] = {**snap["history"], "job": {**self.history_job, "error": busy}}
                return snap
            self.history_job = {"status": "running", "progress": "Starting download", "error": None}

            async def run():
                try:
                    await download_history(settings.model_dir, progress=self.history_job)
                except Exception as exc:
                    self.history_job.update(status="error", error=str(exc))

            self._history_task = asyncio.create_task(run())
            return self.snapshot()

    async def start_train(self) -> dict:
        async with self._lock:
            busy = self._busy("train")
            if busy:
                raise ValueError(busy)
            self.train_job = {"status": "running", "progress": "Starting training", "error": None, "result": None}

            async def run():
                try:
                    meta = await asyncio.to_thread(train_eurusd, settings.model_dir, progress=self.train_job)
                    self.train_job["result"] = meta
                except Exception as exc:
                    self.train_job.update(status="error", error=str(exc), progress="Training failed")

            self._train_task = asyncio.create_task(run())
            return self.snapshot()


forex_jobs = ForexJobs()
