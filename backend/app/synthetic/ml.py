"""Causal R_100 training, out-of-time evaluation and fail-closed inference.

The next closed candle's open/close is a research proxy for a one-minute
contract. It does not reproduce tick-level purchase/settlement or live payouts.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import time
import uuid
from pathlib import Path

import numpy as np
import pandas as pd

from app.synthetic.analysis import FEATURE_COLUMNS, _COMPUTE, feature_table, features_from_frames
from app.synthetic.history import SYMBOL, TIMEFRAMES, clean_candles, history_status, load_frames

DURATION = 60
FEATURE_SCHEMA = "r100-frama-range-1m-v3"
LABEL_KIND = "next_m1_open_close_proxy_ties_lose"
POLICY_VERSION = "fixed60-purged3-holdout20-v1"
DEFAULT_THRESHOLD = 0.60
ASSUMED_NET_PAYOUT = 0.80
ASSUMED_COST_PER_STAKE = 0.01
MIN_DATASET_SAMPLES = 5000
MIN_FINAL_SELECTED = 100
MIN_FOLD_SELECTED = 30
MIN_WALK_FORWARD_SELECTED = 200
PROBABILITY_NOTE = (
    "Uncalibrated model probability estimates, not guaranteed win rates. "
    "Labels use next-candle open/close; actual entry ticks, settlement ticks and payout can differ."
)
_MODEL_CACHE: dict[str, tuple[int, object]] = {}
_DIAGNOSTIC_CACHE: dict[tuple[str, int, int, float], dict] = {}


def artifact_paths(base: str | Path) -> dict[str, Path]:
    root = Path(base) / "synthetic_r100"
    return {
        "root": root, "candidates": root / "candidates",
        "active": root / "active.json", "latest": root / "latest_report.json",
    }


def _json_write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _json_read(path: Path) -> dict | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Model metadata must be an object")
    return payload


def label_outcome(entry_price: float, exit_price: float, direction: str) -> str:
    """Equal prices lose both strict Rise and Fall contracts."""
    if direction not in {"RISE", "FALL"}:
        raise ValueError("Direction must be RISE or FALL")
    if not all(math.isfinite(float(value)) and float(value) > 0 for value in (entry_price, exit_price)):
        raise ValueError("Label prices must be finite and positive")
    won = exit_price > entry_price if direction == "RISE" else exit_price < entry_price
    return "win" if won else "loss"


def publish_train_job(model_base: str | Path, job: dict) -> None:
    """Remember the train stage on disk. The in-memory job disappears if the process is killed."""
    payload = {
        "status": job.get("status") or "idle",
        "progress": job.get("progress") if isinstance(job.get("progress"), str) else "",
        "error": job.get("error") if isinstance(job.get("error"), str) else None,
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
    }
    try:
        _json_write(Path(model_base) / "synthetic_r100" / "train_job.json", payload)
    except Exception:
        return


def build_dataset(frames: dict, *, now: int | None = None, progress: dict | None = None,
                  on_progress=None) -> pd.DataFrame:
    """Use the exact live feature computation and only an aligned next candle.

    Class 0 = FALL, 1 = RISE, 2 = TIE. Ties are never converted into FALL.
    The current feature candle and the outcome candle must be contiguous.
    """
    cutoff = int(time.time()) if now is None else int(now)
    cleaned = {tf: clean_candles(frames.get(tf, []), step, now=cutoff)
               for tf, step in TIMEFRAMES.items()}
    features = feature_table(cleaned, now=cutoff, progress=progress, on_progress=on_progress)
    if features.empty:
        return pd.DataFrame()
    entry = pd.DataFrame(cleaned["1m"])
    labels = entry[["epoch", "open", "close"]].rename(columns={
        "epoch": "decision_epoch", "open": "entry_price", "close": "exit_price",
    })
    dataset = features.merge(labels, on="decision_epoch", how="inner", validate="one_to_one")
    prior_epochs = set(int(row["epoch"]) for row in cleaned["1m"])
    dataset = dataset[dataset["decision_epoch"].map(lambda epoch: int(epoch) - DURATION in prior_epochs)].copy()
    dataset["entry_epoch"] = dataset["decision_epoch"].astype("int64")
    dataset["expiry_epoch"] = dataset["entry_epoch"] + DURATION
    dataset["label"] = np.where(dataset["exit_price"] > dataset["entry_price"], 1,
                                np.where(dataset["exit_price"] < dataset["entry_price"], 0, 2))
    finite = np.isfinite(dataset[list(FEATURE_COLUMNS)].to_numpy(dtype=float)).all(axis=1)
    return dataset[finite].sort_values("decision_epoch").reset_index(drop=True)


def purged_walk_forward(dataset: pd.DataFrame, *, folds: int = 3,
                        test_fraction: float = 0.2) -> tuple[list[tuple[pd.DataFrame, pd.DataFrame]], pd.DataFrame, pd.DataFrame]:
    """Expanding chronological folds, then one fixed untouched final test.

    Purge labels ending at or after each validation/test decision. The stricter
    '<' boundary removes the candle touching the first held-out decision.
    """
    ordered = dataset.sort_values("decision_epoch").reset_index(drop=True)
    if folds < 3 or not 0.1 <= test_fraction <= 0.3 or len(ordered) < 100:
        raise ValueError("Need at least 100 chronological rows, 3 folds, and a 10–30% final test")
    if ordered["decision_epoch"].duplicated().any():
        raise ValueError("Duplicate decision timestamps")
    boundary = int(len(ordered) * (1 - test_fraction))
    test = ordered.iloc[boundary:].copy()
    test_start = int(test["decision_epoch"].min())
    development = ordered.iloc[:boundary]
    development = development[development["expiry_epoch"] < test_start].copy()
    first_validation = int(len(development) * 0.4)
    boundaries = np.linspace(first_validation, len(development), folds + 1, dtype=int)
    splits = []
    for start, end in zip(boundaries, boundaries[1:]):
        validation = development.iloc[start:end].copy()
        if validation.empty:
            raise ValueError("Insufficient rows in a walk-forward fold")
        validation_start = int(validation["decision_epoch"].min())
        train = development.iloc[:start]
        train = train[train["expiry_epoch"] < validation_start].copy()
        if train.empty:
            raise ValueError("Insufficient purged training history")
        assert int(train["expiry_epoch"].max()) < validation_start
        splits.append((train, validation))
    assert int(development["expiry_epoch"].max()) < test_start
    return splits, development, test


def wilson_lower(hits: int, total: int, *, z: float = 1.96) -> float | None:
    if total <= 0:
        return None
    proportion = hits / total
    denominator = 1 + z * z / total
    center = proportion + z * z / (2 * total)
    margin = z * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total))
    return float((center - margin) / denominator)


def _probabilities(model, values: np.ndarray) -> np.ndarray:
    """Map a classifier's fitted classes into explicit FALL/RISE/TIE columns."""
    raw = np.asarray(model.predict_proba(values), dtype=float)
    classes = np.asarray(model.classes_)
    out = np.zeros((len(values), 3), dtype=float)
    if classes.ndim != 1 or len(set(classes.tolist())) != len(classes) or not np.isin(classes, [0, 1, 2]).all():
        raise ValueError("Invalid classifier outcome classes")
    if raw.ndim != 2 or raw.shape != (len(values), len(classes)):
        raise ValueError("Invalid classifier probability shape")
    for index, label in enumerate(classes):
        if int(label) not in (0, 1, 2):
            raise ValueError("Unknown classifier outcome class")
        out[:, int(label)] = raw[:, index]
    if not np.isfinite(out).all() or (out < 0).any() or (out > 1).any():
        raise ValueError("Non-finite or out-of-range classifier probabilities")
    if not np.allclose(out.sum(axis=1), 1.0, atol=1e-6):
        raise ValueError("Classifier probabilities do not sum to one")
    return out


def policy_selection(probabilities: np.ndarray, dataset: pd.DataFrame, *,
                     threshold: float = DEFAULT_THRESHOLD) -> tuple[np.ndarray, np.ndarray]:
    """The same agreement rule used by live inference, including all rule gates."""
    direction = np.zeros(len(probabilities), dtype=int)
    direction[probabilities[:, 1] >= threshold] = 1
    direction[probabilities[:, 0] >= threshold] = -1
    technical = dataset["checklist_direction"].to_numpy(dtype=int)
    eligible = dataset["rule_eligible"].to_numpy(dtype=float) == 1
    selected = eligible & (direction != 0) & (direction == technical)
    return direction, selected


def selection_diagnostics(probabilities: np.ndarray, dataset: pd.DataFrame, *,
                          threshold: float = DEFAULT_THRESHOLD) -> dict:
    """Explain abstentions without changing the predeclared selection policy."""
    direction, selected = policy_selection(probabilities, dataset, threshold=threshold)
    confidence = np.maximum(probabilities[:, 0], probabilities[:, 1])
    confident = direction != 0
    eligible = dataset["rule_eligible"].to_numpy(dtype=float) == 1
    joint = confident & eligible
    conflicts = joint & ~selected
    count = int(selected.sum())
    if count:
        reason = f"{count:,} samples met the confidence threshold and all technical rules in the same direction."
    elif not confident.any():
        reason = f"No model estimate reached the {threshold:.0%} confidence threshold; no trades were selected."
    elif not eligible.any():
        reason = "No samples passed all technical rules; no trades were selected."
    elif not joint.any():
        reason = "Confidence and technical rules never passed on the same sample; no trades were selected."
    else:
        reason = "Confident estimates disagreed with the technical direction; no trades were selected."
    return {
        "confidence_passed": int(confident.sum()), "rule_eligible": int(eligible.sum()),
        "confidence_and_rules": int(joint.sum()), "direction_conflicts": int(conflicts.sum()),
        "selected": count, "below_threshold": int((~confident).sum()),
        "technical_blocked": int((confident & ~eligible).sum()),
        "confidence_min": float(confidence.min()) if len(confidence) else None,
        "confidence_median": float(np.median(confidence)) if len(confidence) else None,
        "confidence_p95": float(np.quantile(confidence, 0.95)) if len(confidence) else None,
        "confidence_max": float(confidence.max()) if len(confidence) else None,
        "reason": reason,
    }


def evaluate(probabilities: np.ndarray, dataset: pd.DataFrame, *,
             threshold: float = DEFAULT_THRESHOLD) -> dict:
    from sklearn.metrics import roc_auc_score

    truth = dataset["label"].to_numpy(dtype=int)
    direction, selected = policy_selection(probabilities, dataset, threshold=threshold)
    # A binary trade must choose a side even if the model expects a tie.
    forced = np.where(probabilities[:, 1] >= probabilities[:, 0], 1, 0)
    wins = np.where(direction == 1, truth == 1, truth == 0)
    count = int(selected.sum())
    hits = int(wins[selected].sum())
    accuracy = hits / count if count else None
    non_ties = truth != 2
    auc = None
    if len(np.unique(truth[non_ties])) == 2:
        directional = probabilities[non_ties, 1] / np.maximum(
            probabilities[non_ties, 0] + probabilities[non_ties, 1], 1e-12)
        auc = float(roc_auc_score(truth[non_ties], directional))
    actual = np.eye(3)[truth]
    payouts = []
    for payout in (0.70, 0.80, 0.90, 0.95):
        payouts.append({
            "net_payout": payout, "cost_per_stake": ASSUMED_COST_PER_STAKE,
            "break_even_accuracy": (1 + ASSUMED_COST_PER_STAKE) / (1 + payout),
            "expected_return_per_stake": None if accuracy is None else (
                accuracy * payout - (1 - accuracy) - ASSUMED_COST_PER_STAKE),
        })
    return {
        "samples": len(dataset), "accuracy": float((forced == truth).mean()),
        "auc": auc, "auc_excludes_ties": True,
        "brier": float(np.mean(np.sum((actual - probabilities) ** 2, axis=1)) / 2),
        "brier_definition": "Half mean squared error over FALL, RISE and TIE probabilities",
        "ties": int((truth == 2).sum()), "threshold": threshold,
        "selected": count, "selected_wins": hits, "selected_accuracy": accuracy,
        "coverage": float(selected.mean()), "selected_wilson_lower": wilson_lower(hits, count),
        "selected_ties": int(((truth == 2) & selected).sum()),
        "selected_rise": int(((direction == 1) & selected).sum()),
        "selected_fall": int(((direction == -1) & selected).sum()),
        "selection_diagnostics": selection_diagnostics(probabilities, dataset, threshold=threshold),
        "payout_sensitivity": payouts,
    }


def acceptance(report: dict) -> tuple[bool, list[str]]:
    """Predeclared evidence gates. Never tune these using final-test outcomes."""
    reasons = []
    folds = report.get("walk_forward") or []
    final = report.get("final_test") or {}
    # Comparisons with NaN are false in both directions. Reject malformed
    # evidence explicitly before evaluating promotion thresholds.
    if not isinstance(folds, list) or not isinstance(final, dict) or any(not isinstance(fold, dict) for fold in folds):
        return False, ["Validation evidence has an invalid structure"]

    def valid_rate(value) -> bool:
        return (not isinstance(value, bool) and isinstance(value, (int, float))
                and math.isfinite(value) and 0 <= value <= 1)

    def valid_count(value) -> bool:
        return not isinstance(value, bool) and isinstance(value, int) and value >= 0

    for name, evidence in [("Final test", final), *[(f"Fold {i}", fold) for i, fold in enumerate(folds, 1)]]:
        for model_name in ("catboost", "logistic"):
            metrics = evidence.get(model_name) or {}
            if not isinstance(metrics, dict):
                return False, [f"{name} {model_name} evidence has an invalid structure"]
            rate_fields = ("auc", "brier", "selected_accuracy", "selected_wilson_lower")
            if any(value is not None and not valid_rate(value) for value in (metrics.get(key) for key in rate_fields)):
                reasons.append(f"{name} {model_name} contains non-finite or out-of-range metrics")
            if metrics.get("selected") is not None and not valid_count(metrics["selected"]):
                reasons.append(f"{name} {model_name} selected count is invalid")
        paired_rate = evidence.get("control_on_candidate_selection_accuracy")
        if paired_rate is not None and not valid_rate(paired_rate):
            reasons.append(f"{name} paired control accuracy is invalid")
    if reasons:
        return False, reasons
    candidate = final.get("catboost") or {}
    control = final.get("logistic") or {}
    breakeven = (1 + ASSUMED_COST_PER_STAKE) / (1 + ASSUMED_NET_PAYOUT)
    if len(folds) < 3:
        reasons.append("At least three purged walk-forward folds are required")
    if int(candidate.get("selected") or 0) < MIN_FINAL_SELECTED:
        reasons.append(f"Final test needs at least {MIN_FINAL_SELECTED} selected trades")
    if candidate.get("selected_wilson_lower") is None or candidate["selected_wilson_lower"] <= breakeven:
        reasons.append("Final selected-trade 95% lower bound does not clear payout/cost break-even")
    if candidate.get("auc") is None or candidate["auc"] <= 0.5:
        reasons.append("Final directional AUC must exceed 0.50")
    if candidate.get("brier") is None or control.get("brier") is None or candidate["brier"] >= control["brier"]:
        reasons.append("CatBoost must improve final Brier score over Logistic Regression")
    paired = final.get("control_on_candidate_selection_accuracy")
    selected_accuracy = candidate.get("selected_accuracy")
    if paired is None or selected_accuracy is None or selected_accuracy <= paired:
        reasons.append("CatBoost must beat the control on the same final selected trades")
    selected_total = sum(int((fold.get("catboost") or {}).get("selected") or 0) for fold in folds)
    if selected_total < MIN_WALK_FORWARD_SELECTED:
        reasons.append(f"Walk-forward testing needs {MIN_WALK_FORWARD_SELECTED} selected trades in total")
    fold_accuracies = []
    brier_improvements = 0
    for index, fold in enumerate(folds, 1):
        cat = fold.get("catboost") or {}
        logistic = fold.get("logistic") or {}
        if int(cat.get("selected") or 0) < MIN_FOLD_SELECTED:
            reasons.append(f"Fold {index} needs at least {MIN_FOLD_SELECTED} selected trades")
        rate = cat.get("selected_accuracy")
        if rate is None or rate <= breakeven:
            reasons.append(f"Fold {index} selected accuracy does not clear payout/cost break-even")
        else:
            fold_accuracies.append(float(rate))
        if cat.get("brier") is not None and logistic.get("brier") is not None and cat["brier"] < logistic["brier"]:
            brier_improvements += 1
    if folds and brier_improvements < math.ceil(len(folds) * 2 / 3):
        reasons.append("CatBoost must beat the control Brier score in at least two thirds of folds")
    if len(fold_accuracies) >= 3 and max(fold_accuracies) - min(fold_accuracies) > 0.15:
        reasons.append("Selected-trade accuracy is unstable across folds (range exceeds 15 points)")
    return not reasons, reasons


def _models(iterations: int, threads: int):
    from catboost import CatBoostClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    candidate = CatBoostClassifier(
        iterations=iterations, depth=5, learning_rate=0.035, l2_leaf_reg=8,
        loss_function="MultiClass", classes_count=3, random_seed=41,
        thread_count=threads, verbose=False, allow_writing_files=False,
    )
    control = Pipeline([
        ("scaler", StandardScaler()),
        ("logistic", LogisticRegression(max_iter=600, C=0.25, random_state=41)),
    ])
    return candidate, control


def _fit_evaluate(train: pd.DataFrame, validation: pd.DataFrame, *,
                  iterations: int, threads: int) -> tuple[object, object, dict, pd.DataFrame]:
    if not {0, 1}.issubset(set(train["label"].unique())):
        raise ValueError("Training window needs both RISE and FALL outcomes")
    candidate, control = _models(iterations, threads)
    x_train = train[list(FEATURE_COLUMNS)].to_numpy(dtype=float)
    y_train = train["label"].to_numpy(dtype=int)
    x_validation = validation[list(FEATURE_COLUMNS)].to_numpy(dtype=float)
    # No held-out eval_set/early stopping: the final test cannot choose a model.
    candidate.fit(x_train, y_train)
    control.fit(x_train, y_train)
    cat_prob = _probabilities(candidate, x_validation)
    log_prob = _probabilities(control, x_validation)
    _, selected = policy_selection(cat_prob, validation)
    truth = validation["label"].to_numpy(dtype=int)
    log_direction = np.where(log_prob[:, 1] >= log_prob[:, 0], 1, 0)
    result = {
        "train_samples": len(train), "validation_samples": len(validation),
        "train_decision_end": int(train["decision_epoch"].max()),
        "train_label_end": int(train["expiry_epoch"].max()),
        "validation_start": int(validation["decision_epoch"].min()),
        "validation_end": int(validation["decision_epoch"].max()),
        "catboost": evaluate(cat_prob, validation), "logistic": evaluate(log_prob, validation),
        "control_on_candidate_selection_accuracy": float((log_direction[selected] == truth[selected]).mean()) if selected.any() else None,
    }
    predictions = validation[["decision_epoch", "expiry_epoch", "label", "checklist_direction", "rule_eligible"]].copy()
    for name, probabilities in (("catboost", cat_prob), ("logistic", log_prob)):
        for index, label in enumerate(("fall", "rise", "tie")):
            predictions[f"{name}_p_{label}"] = probabilities[:, index]
        predictions[f"{name}_selected"] = policy_selection(probabilities, validation)[1].astype(int)
    return candidate, control, result, predictions


def train_model(model_base: str | Path, *, progress: dict | None = None,
                iterations: int = 250, threads: int = 2) -> dict:
    """Train a candidate and retain evidence even when promotion is rejected."""
    if not 20 <= int(iterations) <= 1000 or not 1 <= int(threads) <= 4:
        raise ValueError("Use 20–1000 iterations and 1–4 CPU threads")
    job = progress if progress is not None else {}
    job.update(status="running", progress="Checking R_100 history", error=None, started_at=int(time.time()))

    def note(message: str) -> None:
        job["progress"] = message
        publish_train_job(model_base, job)

    try:
        publish_train_job(model_base, job)
        with _COMPUTE:
            return _train_locked(model_base, job, note, iterations=int(iterations), threads=int(threads))
    except Exception as exc:
        job.update(status="error", progress="Training failed", error=f"{type(exc).__name__}: {exc}",
                   finished_at=int(time.time()))
        publish_train_job(model_base, job)
        raise


def _train_locked(model_base, job, note, *, iterations: int, threads: int) -> dict:
    import joblib

    try:
        history = history_status(model_base)
        if not history["ready_for_train"]:
            raise ValueError("Download at least 60 days of R_100 1m, 5m and 15m history first")
        note("Building shared closed-candle features and next-one-minute labels")
        frames = load_frames(model_base)
        feature_origins = {tf: int(rows[0]["epoch"]) for tf, rows in frames.items() if rows}
        dataset = build_dataset(frames, progress=job, on_progress=lambda: publish_train_job(model_base, job))
        import gc
        del frames
        gc.collect()
        if len(dataset) < MIN_DATASET_SAMPLES:
            raise ValueError(f"Need at least {MIN_DATASET_SAMPLES} valid labeled rows; found {len(dataset)}")
        folds, development, final_test = purged_walk_forward(dataset)
        fold_results, fold_predictions = [], []
        for index, (train, validation) in enumerate(folds, 1):
            note(f"Walk-forward fold {index}/{len(folds)}: CatBoost versus Logistic Regression")
            _, _, metrics, predictions = _fit_evaluate(train, validation, iterations=iterations, threads=threads)
            metrics["fold"] = index
            predictions["fold"] = index
            fold_results.append(metrics)
            fold_predictions.append(predictions)
        note("Fitting on development history; evaluating untouched final 20%")
        candidate, control, final_metrics, predictions = _fit_evaluate(
            development, final_test, iterations=iterations, threads=threads)
        final_metrics.update(samples=len(final_test), decision_start=int(final_test["decision_epoch"].min()))
        paths = artifact_paths(model_base)
        candidate_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex[:8]
        candidate_dir = paths["candidates"] / candidate_id
        candidate_dir.mkdir(parents=True, exist_ok=False)
        model_path = candidate_dir / "catboost.cbm"
        candidate.save_model(str(model_path))
        joblib.dump(control, candidate_dir / "logistic.joblib")
        predictions.to_csv(candidate_dir / "final_predictions.csv", index=False)
        pd.concat(fold_predictions, ignore_index=True).to_csv(candidate_dir / "walk_forward_predictions.csv", index=False)
        report = {
            "symbol": SYMBOL, "duration_seconds": DURATION, "entry_timeframe": "1m",
            "context_timeframes": ["5m", "15m"], "feature_schema": FEATURE_SCHEMA,
            "features": list(FEATURE_COLUMNS), "label_kind": LABEL_KIND,
            "feature_origins": feature_origins,
            "model_kind": "CatBoostClassifier", "policy_version": POLICY_VERSION,
            "threshold": DEFAULT_THRESHOLD, "trained_at": int(time.time()),
            "candidate_id": candidate_id, "candidate_dir": str(candidate_dir),
            "model_sha256": hashlib.sha256(model_path.read_bytes()).hexdigest(),
            "model_parameters": {"iterations": iterations, "depth": 5, "learning_rate": 0.035, "threads": threads},
            "dataset": {
                "samples": len(dataset), "ties": int((dataset["label"] == 2).sum()),
                "decision_start": int(dataset["decision_epoch"].min()),
                "decision_end": int(dataset["decision_epoch"].max()),
                "development_samples": len(development), "test_samples": len(final_test),
            },
            "walk_forward": fold_results, "final_test": final_metrics,
            "economic_assumptions": {
                "net_payout": ASSUMED_NET_PAYOUT, "cost_per_stake": ASSUMED_COST_PER_STAKE,
                "note": "Sensitivity scenarios, not a fetched contract quote. Verify the actual payout before trading.",
            },
            "validation_note": (
                "Three expanding purged folds and a fixed final 20% period. "
                "No random split, final-test tuning or early stopping. "
                "Repeated training on the same history reuses this test period and is not new independent evidence."
            ),
            "probability_note": PROBABILITY_NOTE, "promoted": False,
        }
        accepted, reasons = acceptance(report)
        report.update(accepted=accepted, acceptance_reasons=reasons,
                      acceptance_reason="All predeclared evidence gates passed" if accepted else "; ".join(reasons))
        # The evaluated development model is promoted as-is; do not refit it
        # on the final test and silently replace the evaluated artifact.
        if accepted:
            report.update(promoted=True, promoted_at=int(time.time()))
        _json_write(candidate_dir / "report.json", report)
        _json_write(paths["latest"], report)
        if accepted:
            _json_write(paths["active"], report)
        job.update(status="done", progress="Validated CatBoost promoted" if accepted else "Candidate saved; evidence gates did not pass",
                   error=None, result=report, finished_at=int(time.time()))
        publish_train_job(model_base, job)
        return report
    except Exception as exc:
        job.update(status="error", progress="Training failed", error=f"{type(exc).__name__}: {exc}",
                   finished_at=int(time.time()))
        publish_train_job(model_base, job)
        raise


def _schema_compatible(meta: dict) -> tuple[bool, str]:
    bindings = {
        "symbol": SYMBOL, "duration_seconds": DURATION, "entry_timeframe": "1m",
        "context_timeframes": ["5m", "15m"], "feature_schema": FEATURE_SCHEMA,
        "features": list(FEATURE_COLUMNS),
        "model_kind": "CatBoostClassifier", "policy_version": POLICY_VERSION,
        "threshold": DEFAULT_THRESHOLD,
    }
    for name, expected in bindings.items():
        if meta.get(name) != expected:
            return False, f"Incompatible model {name}"
    # Earlier 1m artifacts retained the old m5 label name despite using the
    # same one-minute labels. The timeframe/schema checks above disallow 5m
    # models; accepting the legacy name here does not change model semantics.
    if meta.get("label_kind") not in {LABEL_KIND, "next_m5_open_close_proxy_ties_lose"}:
        return False, "Incompatible model label_kind"
    origins = meta.get("feature_origins")
    if not isinstance(origins, dict) or set(origins) != set(TIMEFRAMES):
        return False, "Model does not bind the recursive feature history origins"
    if any(isinstance(origins[tf], bool) or not isinstance(origins[tf], int)
           or origins[tf] < 0 or origins[tf] % step for tf, step in TIMEFRAMES.items()):
        return False, "Model feature history origins are invalid"
    return True, "Candidate schema matches the current one-minute strategy"


def _compatible(meta: dict) -> tuple[bool, str]:
    compatible, reason = _schema_compatible(meta)
    if not compatible:
        return False, reason
    if meta.get("promoted") is not True or meta.get("accepted") is not True:
        return False, "CatBoost candidate has not passed promotion gates"
    accepted, reasons = acceptance(meta)
    if not accepted:
        return False, "Stored validation evidence fails current gates: " + "; ".join(reasons)
    return True, "Validated CatBoost is available"


def _model_path(base: str | Path, meta: dict) -> Path:
    candidate_id = meta.get("candidate_id")
    if not isinstance(candidate_id, str) or not candidate_id or any(char not in "0123456789TZ-abcdef" for char in candidate_id):
        raise ValueError("Invalid model candidate ID")
    return artifact_paths(base)["candidates"] / candidate_id / "catboost.cbm"


def _saved_report_diagnostics(model_base: str | Path, report: dict | None) -> None:
    """Enrich older reports from their saved predictions, never alter evidence.

    Cache by file identity so status polling does not repeatedly parse a large
    CSV. A missing or invalid optional file cannot disable model status.
    """
    if not report or not isinstance(report.get("final_test"), dict):
        return
    final = report["final_test"]
    metrics = [final.get(name) for name in ("catboost", "logistic")]
    if all(isinstance(item, dict) and item.get("selection_diagnostics") for item in metrics):
        return
    try:
        path = _model_path(model_base, report).parent / "final_predictions.csv"
        stat = path.stat()
        if stat.st_size > 100_000_000:
            return
        threshold = float(report["threshold"])
        if not math.isfinite(threshold) or not 0.5 < threshold <= 1:
            return
        key = (str(path), stat.st_mtime_ns, stat.st_size, threshold)
        cached = _DIAGNOSTIC_CACHE.get(key)
        if cached is None:
            columns = ["checklist_direction", "rule_eligible", *[
                f"{name}_p_{side}" for name in ("catboost", "logistic") for side in ("fall", "rise", "tie")
            ]]
            predictions = pd.read_csv(path, usecols=columns)
            if predictions.empty or not np.isfinite(predictions.to_numpy(dtype=float)).all():
                return
            cached = {}
            for name in ("catboost", "logistic"):
                probability = predictions[[f"{name}_p_{side}" for side in ("fall", "rise", "tie")]].to_numpy(dtype=float)
                if (probability < 0).any() or (probability > 1).any() or not np.allclose(probability.sum(axis=1), 1.0, atol=1e-6):
                    return
                cached[name] = selection_diagnostics(probability, predictions, threshold=threshold)
            cached["samples"] = len(predictions)
            _DIAGNOSTIC_CACHE.clear()
            _DIAGNOSTIC_CACHE[key] = cached
        for name in ("catboost", "logistic"):
            evidence = final.get(name)
            if (isinstance(evidence, dict) and evidence.get("samples") == cached["samples"]
                    and evidence.get("selected") == cached[name]["selected"]):
                evidence["selection_diagnostics"] = cached[name]
    except (OSError, ValueError, TypeError, KeyError):
        return


def ml_status(model_base: str | Path) -> dict:
    paths = artifact_paths(model_base)
    result = {
        "available": False, "ready": False, "promoted": False, "compatible": False,
        "state": "inactive", "reason": "Download history and train a validated CatBoost model",
        "threshold": DEFAULT_THRESHOLD, "trained_at": None, "metadata": None, "report": None,
        "probability_note": PROBABILITY_NOTE,
    }
    try:
        result["report"] = _json_read(paths["latest"])
        _saved_report_diagnostics(model_base, result["report"])
        meta = _json_read(paths["active"])
        if meta is None:
            if result["report"]:
                result["trained_at"] = result["report"].get("trained_at")
                compatible, reason = _schema_compatible(result["report"])
                if not compatible:
                    return {**result, "state": "invalid", "reason": f"{reason}; retrain for the current one-minute strategy"}
                candidate_path = _model_path(model_base, result["report"])
                if not candidate_path.is_file():
                    return {**result, "state": "invalid", "reason": "Candidate model file is missing; retrain to restore confidence estimates"}
                if hashlib.sha256(candidate_path.read_bytes()).hexdigest() != result["report"].get("model_sha256"):
                    return {**result, "state": "invalid", "reason": "Candidate model checksum does not match its report; retrain to restore confidence estimates"}
                result.update(state="candidate", reason="Latest candidate did not pass validation; no model is promoted")
            return result
        result.update(metadata=meta, trained_at=meta.get("trained_at"))
        compatible, reason = _compatible(meta)
        if not compatible:
            return {**result, "state": "invalid", "reason": reason}
        path = _model_path(model_base, meta)
        if not path.is_file():
            return {**result, "state": "invalid", "reason": "Promoted model file is missing"}
        if hashlib.sha256(path.read_bytes()).hexdigest() != meta.get("model_sha256"):
            return {**result, "state": "invalid", "reason": "Promoted model checksum does not match validation metadata"}
        return {**result, "available": True, "ready": True, "compatible": True,
                "promoted": True, "state": "ready", "reason": reason}
    except Exception as exc:
        return {**result, "state": "invalid", "reason": f"Cannot validate model artifacts ({type(exc).__name__})"}


def _display_meta(model_base: str | Path, status: dict) -> dict | None:
    """Promoted model, or the latest saved candidate for a chart reading only."""
    if status.get("ready") and isinstance(status.get("metadata"), dict):
        return status["metadata"]
    report = status.get("report")
    if not isinstance(report, dict) or not _schema_compatible(report)[0]:
        return None
    try:
        path = _model_path(model_base, report)
    except ValueError:
        return None
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != report.get("model_sha256"):
        return None
    return report


def predict_frames(model_base: str | Path, frames: dict, *, now: int,
                   checklist_direction: str, analysis: dict | None = None) -> dict:
    status = ml_status(model_base)
    result = {
        **status, "direction": "NO TRADE", "p_rise": None, "p_fall": None, "p_tie": None,
        "confidence": None, "probability": None, "agrees": False, "hybrid_passed": False,
        "inference_available": False, "display_only": not status["ready"],
        "prediction_candidate_id": None, "decision_epoch": None,
    }
    try:
        meta = _display_meta(model_base, status)
        if meta is None:
            return result
        # Recursive indicators must retain their training seed. Extra older
        # history is cropped; missing original history requires retraining.
        origins = meta["feature_origins"]
        seeded_frames = {}
        same_origins = True
        for tf, step in TIMEFRAMES.items():
            clean = clean_candles(frames.get(tf, []), step, now=now)
            same_origins = same_origins and bool(clean) and clean[0]["epoch"] == origins[tf]
            seeded = [bar for bar in clean if bar["epoch"] >= origins[tf]]
            if not seeded or seeded[0]["epoch"] != origins[tf]:
                return {**result, "reason": f"{tf} history origin differs from training; restore history or retrain"}
            seeded_frames[tf] = seeded
        decision = seeded_frames["1m"][-1]["epoch"] + DURATION
        row = None
        if (same_origins and isinstance(analysis, dict) and analysis.get("decision_epoch") == decision
                and 0 <= now - decision < DURATION + 30 and analysis.get("features")):
            row = analysis["features"]
        if row is None:
            row = features_from_frames(seeded_frames, now=now)
        if row is None:
            return {**result, "reason": "Need sufficient contiguous closed candles on all three timeframes"}
        values = np.asarray([[float(row[name]) for name in FEATURE_COLUMNS]], dtype=float)
        if not np.isfinite(values).all():
            raise ValueError("Non-finite live features")
        path = _model_path(model_base, meta)
        stamp = path.stat().st_mtime_ns
        cached = _MODEL_CACHE.get(str(path))
        if cached is None or cached[0] != stamp:
            from catboost import CatBoostClassifier

            model = CatBoostClassifier()
            model.load_model(str(path))
            _MODEL_CACHE.clear()
            _MODEL_CACHE[str(path)] = (stamp, model)
        else:
            model = cached[1]
        probabilities = _probabilities(model, values)[0]
        p_fall, p_rise, p_tie = (float(value) for value in probabilities)
        direction = "RISE" if p_rise >= DEFAULT_THRESHOLD else "FALL" if p_fall >= DEFAULT_THRESHOLD else "NO TRADE"
        internal_direction = 1 if direction == "RISE" else -1 if direction == "FALL" else 0
        eligible = float(row.get("rule_eligible", 0)) == 1
        agrees = (status.get("ready") is True and direction in {"RISE", "FALL"} and direction == checklist_direction
                  and internal_direction == int(row.get("checklist_direction", 0)) and eligible)
        if agrees:
            reason = "Validated CatBoost agrees with the closed-candle strategy"
        elif status.get("ready") is True:
            reason = "CatBoost confidence and full technical confirmation do not agree"
        else:
            reason = "Display only. This candidate did not pass validation, so it cannot place a trade."
        return {
            **result, "direction": direction, "p_rise": p_rise, "p_fall": p_fall, "p_tie": p_tie,
            "confidence": max(p_fall, p_rise), "probability": max(p_fall, p_rise),
            "agrees": agrees, "hybrid_passed": bool(agrees),
            "inference_available": True, "display_only": not status["ready"],
            "prediction_candidate_id": meta["candidate_id"], "decision_epoch": decision,
            "reason": reason,
            "features_used": list(FEATURE_COLUMNS),
        }
    except Exception as exc:
        return {**result, "ready": False, "state": "unavailable",
                "reason": f"Model inference unavailable ({type(exc).__name__})"}


def main() -> None:
    parser = argparse.ArgumentParser(description="R_100 one-minute CatBoost research pipeline")
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--train", action="store_true")
    parser.add_argument("--iterations", type=int, default=250)
    parser.add_argument("--threads", type=int, default=2)
    args = parser.parse_args()
    if args.download:
        from app.synthetic.history import download_history
        print(json.dumps(asyncio.run(download_history(args.model_dir, days=args.days)), indent=2), flush=True)
    if args.train:
        print(json.dumps(train_model(args.model_dir, iterations=args.iterations, threads=args.threads), indent=2), flush=True)
    if not args.download and not args.train:
        print(json.dumps(ml_status(args.model_dir), indent=2), flush=True)


if __name__ == "__main__":
    main()
