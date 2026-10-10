"""Background training. This process never places orders."""

from __future__ import annotations

import time

import numpy as np
import structlog

from app.config import settings
from app.digitmatch.features import build_examples
from app.digitmatch.registry import save_bundle
from app.digitmatch.store import SqlStore
from app.digitmatch.training import run_training
from app.models.digitmatch import DmTrainJob

logger = structlog.get_logger(__name__)
_BLOCKING = {"gap", "conflict", "out_of_order", "precision_change", "replay", "precision_unknown", "digit_unreadable"}


def _load(store: SqlStore, symbol: str):
    rows = [row for row in store.series(symbol) if row.digit_value is not None]
    if not rows:
        return None
    digits, prices, usable, epochs = [], [], [], []
    for row in rows:
        flags = set(filter(None, (row.quality_flags or "").split(",")))
        digits.append(int(row.digit_value))
        prices.append(float(row.quote_text))
        usable.append(not (flags & _BLOCKING))
        epochs.append(int(row.broker_epoch))
    return (
        np.asarray(digits),
        np.asarray(prices, dtype=float),
        np.asarray(usable, dtype=bool),
        np.asarray(epochs),
    )


def run_once(store: SqlStore, symbol: str, enable_mlp: bool, job_id: int | None = None) -> int:
    def progress(text: str) -> None:
        if job_id is not None:
            store.save_job(DmTrainJob, job_id, status="running", progress=text)

    progress("Loading ticks")
    loaded = _load(store, symbol)
    if loaded is None:
        raise RuntimeError("no ticks are stored for training")
    digits, prices, usable, epochs = loaded
    progress(f"Building features from {len(digits)} ticks")

    def on_progress(done: int, total: int) -> None:
        progress(f"Building features, {done} of {total} ticks")

    examples = build_examples(digits, prices, usable, on_progress=on_progress)
    if len(examples["y"]) < 40:
        raise RuntimeError("not enough clean five-tick examples to allocate train, calibration, selection, and test")
    progress(f"Fitting models on {len(examples['y'])} examples")
    bundle = run_training(
        digits=digits,
        examples=examples,
        epochs=epochs,
        seed=settings.dm_train_seed,
        enable_mlp=enable_mlp,
        default_margin=settings.dm_default_margin,
    )
    progress("Saving the candidate")
    meta = save_bundle(bundle)
    return store.save_model_row(meta)


def main() -> None:
    store = SqlStore()
    store.create_schema()
    store.seed()
    abandoned = store.abandon_running_trains()
    logger.info("dm_train_worker_started", abandoned_running=abandoned)
    while True:
        job = store.next_job(DmTrainJob)
        if job is None:
            time.sleep(2)
            continue
        store.save_job(DmTrainJob, job["id"], status="running", progress="Starting")
        runtime = store.runtime()
        symbol = runtime.resolved_symbol or "R_100"
        try:
            if job.get("cancel_requested"):
                store.save_job(DmTrainJob, job["id"], status="cancelled")
                continue
            model_id = run_once(store, symbol, bool(job.get("enable_mlp")), job_id=int(job["id"]))
            store.save_job(
                DmTrainJob,
                job["id"],
                status="done",
                progress="candidate saved; not promoted",
                model_id=model_id,
            )
            store.audit("train_complete", f"model {model_id} saved as a candidate")
        except Exception as exc:
            store.save_job(DmTrainJob, job["id"], status="error", error=str(exc)[:500])
            logger.warning("dm_train_failed", error=str(exc)[:200])


if __name__ == "__main__":
    main()
