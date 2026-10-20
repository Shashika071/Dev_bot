"""Train a purged time-split logistic filter for EURUSD 4h Rise/Fall."""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from app.forex.history import history_root, history_status, load_candles
from app.forex.ml.dataset import DURATION, FEATURE_COLUMNS, LABEL_KIND, build_dataset

DEFAULT_THRESHOLD = 0.55
MIN_SELECTED = 30
MIN_SELECTED_ACCURACY = 0.52


def model_dir(base: str | Path) -> Path:
    return Path(base) / "forex_eurusd_rise_fall"


def artifact_paths(base: str | Path) -> dict[str, Path]:
    root = model_dir(base)
    return {
        "root": root,
        "model": root / "model.joblib",
        "meta": root / "meta.json",
        "candidates": root / "candidates",
    }


def load_meta(base: str | Path) -> dict | None:
    path = artifact_paths(base)["meta"]
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def purged_time_split(dataset: pd.DataFrame, *, test_fraction: float = 0.2) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Chronological split that removes train rows whose label overlaps the test window."""
    ordered = dataset.sort_values("decision_epoch").reset_index(drop=True)
    if len(ordered) < 50:
        raise ValueError("Need more labeled samples for a purged split")
    split = max(1, min(len(ordered) - 1, int(len(ordered) * (1.0 - test_fraction))))
    test_start = int(ordered.iloc[split]["decision_epoch"])
    train = ordered[ordered["expiry_epoch"] <= test_start].copy()
    test = ordered[ordered["decision_epoch"] >= test_start].copy()
    if train.empty or test.empty:
        raise ValueError("Purged split removed all train or test rows")
    # Hard guarantee: no train label ends after the first test decision.
    assert int(train["expiry_epoch"].max()) <= int(test["decision_epoch"].min())
    return train, test


def apply_wait_policy(proba_rise: np.ndarray, y_true: np.ndarray, *, threshold: float = DEFAULT_THRESHOLD) -> dict:
    """Score the live RISE/FALL/WAIT gate, not a forced binary cutoff."""
    direction = np.full(len(proba_rise), "WAIT", dtype=object)
    direction[proba_rise >= threshold] = "RISE"
    direction[proba_rise <= (1.0 - threshold)] = "FALL"
    selected = direction != "WAIT"
    coverage = float(selected.mean()) if len(selected) else 0.0
    if not selected.any():
        return {
            "threshold": threshold,
            "coverage": coverage,
            "selected": 0,
            "selected_accuracy": None,
            "selected_rise_hit_rate": None,
            "selected_fall_hit_rate": None,
            "wait_rate": 1.0,
            "forced_binary_accuracy": float(((proba_rise >= threshold).astype(int) == y_true).mean()) if len(y_true) else None,
        }
    chosen = direction[selected]
    truth = y_true[selected]
    hits = np.where(chosen == "RISE", truth == 1, truth == 0)
    rise_mask = chosen == "RISE"
    fall_mask = chosen == "FALL"
    return {
        "threshold": threshold,
        "coverage": coverage,
        "selected": int(selected.sum()),
        "selected_accuracy": float(hits.mean()),
        "selected_rise_hit_rate": float(hits[rise_mask].mean()) if rise_mask.any() else None,
        "selected_fall_hit_rate": float(hits[fall_mask].mean()) if fall_mask.any() else None,
        "wait_rate": float((~selected).mean()),
        "forced_binary_accuracy": float(((proba_rise >= threshold).astype(int) == y_true).mean()),
    }


def acceptance_ok(metrics: dict) -> tuple[bool, str]:
    selected = int(metrics.get("selected") or 0)
    accuracy = metrics.get("selected_accuracy")
    if selected < MIN_SELECTED:
        return False, f"Need at least {MIN_SELECTED} selected WAIT-policy trades (got {selected})"
    if accuracy is None or accuracy < MIN_SELECTED_ACCURACY:
        return False, f"Selected accuracy {accuracy} below minimum {MIN_SELECTED_ACCURACY}"
    baseline = metrics.get("baseline_majority_accuracy")
    if baseline is not None and accuracy < float(baseline):
        return False, f"Selected accuracy {accuracy:.4f} below majority baseline {float(baseline):.4f}"
    return True, "Accepted"


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def promote_candidate(model_base: str | Path, candidate_dir: Path, meta: dict) -> dict:
    paths = artifact_paths(model_base)
    paths["root"].mkdir(parents=True, exist_ok=True)
    model_src = candidate_dir / "model.joblib"
    meta_src = candidate_dir / "meta.json"
    if not model_src.exists() or not meta_src.exists():
        raise FileNotFoundError("Candidate artifacts incomplete")
    tmp_model = paths["model"].with_suffix(".joblib.tmp")
    shutil.copy2(model_src, tmp_model)
    tmp_model.replace(paths["model"])
    promoted = {
        **meta,
        "promoted": True,
        "promoted_at": int(time.time()),
        "candidate_dir": str(candidate_dir),
    }
    _atomic_write_json(paths["meta"], promoted)
    return promoted


def train_eurusd(model_base: str | Path, *, progress: dict | None = None, auto_promote: bool = True) -> dict:
    report = progress if progress is not None else {}
    report.update(status="running", progress="Building dataset", error=None, started_at=int(time.time()))
    hist_root = history_root(model_base)
    status = history_status(model_base)
    if not status["ready_for_train"]:
        raise ValueError("Download EURUSD 15m/1h/4h history before training")
    frames = {tf: load_candles(hist_root, tf) for tf in ("15m", "1h", "4h")}
    dataset = build_dataset(frames["15m"], frames["1h"], frames["4h"], sample_every=4)
    if len(dataset) < 200:
        raise ValueError(f"Need at least 200 labeled samples (got {len(dataset)})")
    report["progress"] = f"Purged split on {len(dataset)} samples"
    train_df, test_df = purged_time_split(dataset)
    x_train = train_df[FEATURE_COLUMNS].to_numpy(dtype=float)
    y_train = train_df["label_rise"].to_numpy(dtype=int)
    x_test = test_df[FEATURE_COLUMNS].to_numpy(dtype=float)
    y_test = test_df["label_rise"].to_numpy(dtype=int)
    pipe = Pipeline([
        ("scaler", StandardScaler()),
        ("clf", LogisticRegression(max_iter=1000, class_weight="balanced")),
    ])
    pipe.fit(x_train, y_train)
    # A stricter cutoff fit on one window did not hold on the next window, so the
    # live gate stays at 0.55 until a later test period clears the promotion bar.
    threshold = DEFAULT_THRESHOLD
    proba = pipe.predict_proba(x_test)[:, 1]
    policy = apply_wait_policy(proba, y_test, threshold=threshold)
    majority = float(max(y_test.mean(), 1.0 - y_test.mean())) if len(y_test) else None
    metrics = {
        "samples": int(len(dataset)),
        "train_samples": int(len(train_df)),
        "test_samples": int(len(test_df)),
        "purge_gap_seconds": DURATION,
        "label_kind": LABEL_KIND,
        "test_auc": float(roc_auc_score(y_test, proba)) if len(np.unique(y_test)) > 1 else None,
        "test_brier": float(brier_score_loss(y_test, proba)) if len(y_test) else None,
        "baseline_majority_accuracy": majority,
        "positive_rate_test": float(y_test.mean()) if len(y_test) else None,
        "train_decision_end": int(train_df["decision_epoch"].max()),
        "test_decision_start": int(test_df["decision_epoch"].min()),
        "train_label_end_max": int(train_df["expiry_epoch"].max()),
        **policy,
    }
    accepted, reason = acceptance_ok(metrics)
    paths = artifact_paths(model_base)
    candidate_dir = paths["candidates"] / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    candidate_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump({"pipeline": pipe, "features": FEATURE_COLUMNS}, candidate_dir / "model.joblib")
    meta = {
        "symbol": "frxEURUSD",
        "duration_seconds": DURATION,
        "features": FEATURE_COLUMNS,
        "threshold": threshold,
        "metrics": metrics,
        "trained_at": int(time.time()),
        "label_kind": LABEL_KIND,
        "accepted": accepted,
        "acceptance_reason": reason,
        "promoted": False,
        "note": (
            "Hybrid candle-proxy filter only. Checklist/news/session still required. "
            "Accuracy is WAIT-policy selected-trade hit rate, not calibrated win probability."
        ),
    }
    _atomic_write_json(candidate_dir / "meta.json", meta)
    result = {**meta, "candidate_dir": str(candidate_dir)}
    if auto_promote and accepted:
        result = promote_candidate(model_base, candidate_dir, meta)
        report.update(status="done", progress=f"Training finished and promoted ({reason})",
                      result=result, finished_at=int(time.time()))
    else:
        report.update(
            status="done",
            progress=f"Candidate saved; not promoted ({reason})",
            result=result,
            finished_at=int(time.time()),
        )
    return result
