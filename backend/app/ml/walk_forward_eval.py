"""Purged expanding-window evaluation helpers.

Each fold fits models, calibrates them on a separate chronological slice, and
scores calibrated predictions against a calibration-period frequency baseline.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss

from app.features.dataset import chronological_split
from app.features.pipeline import apply_feature_prep, fit_feature_prep


def lockbox_start_index(n: int, fraction: float = 0.12, min_lockbox: int = 20) -> int:
    """Index of the newest untouched lockbox. Data must already be chronological."""
    if n <= 1:
        return 0
    n_lock = max(5, int(round(n * float(fraction))))
    if n >= 80:
        n_lock = max(int(min_lockbox), n_lock)
    n_lock = min(n_lock, max(1, n // 5))
    return max(1, n - n_lock)


def outer_test_windows(
    n_dev: int,
    n_splits: int = 5,
    min_train_frac: float = 0.45,
) -> list[tuple[int, int]]:
    """Expanding-prefix test windows on the development portion only."""
    if n_dev < 40 or n_splits < 1:
        return []
    start = int(n_dev * float(min_train_frac))
    remain = n_dev - start
    if start < 15 or remain < n_splits * 5:
        return []
    size = remain // int(n_splits)
    if size < 5:
        return []
    windows: list[tuple[int, int]] = []
    for fold in range(int(n_splits)):
        test_start = start + fold * size
        test_end = n_dev if fold == n_splits - 1 else test_start + size
        if test_end > test_start:
            windows.append((test_start, test_end))
    return windows


def fractions_for_lockbox(n: int, lock_start: int) -> dict[str, float]:
    dev = max(lock_start, 1) / max(n, 1)
    test = max(n - lock_start, 1) / max(n, 1)
    raw = {
        "train_frac": dev * 0.70,
        "val_frac": dev * 0.15,
        "cal_frac": dev * 0.15,
        "test_frac": test,
    }
    total = sum(raw.values())
    return {key: value / total for key, value in raw.items()}


def _brier(y_true, y_prob) -> float | None:
    y = np.asarray(y_true, dtype=float).ravel()
    p = np.clip(np.asarray(y_prob, dtype=float).ravel(), 1e-6, 1 - 1e-6)
    if len(y) < 5 or len(y) != len(p):
        return None
    return float(brier_score_loss(y, p))


def aggregate_walk_forward(
    rows: list[dict[str, Any]],
    *,
    epsilon: float = 0.0,
    min_test: int = 30,
    min_win_fraction: float = 0.8,
) -> dict[str, Any]:
    eligible = [row for row in rows if int(row.get("n_test") or 0) >= int(min_test)]
    names: set[str] = set()
    for row in eligible:
        names.update(key for key in (row.get("briers") or {}) if key != "baseline")
    models: dict[str, dict] = {}
    selected = None
    best_mean = float("inf")
    for name in sorted(names):
        briers: list[float] = []
        wins = 0
        for row in eligible:
            scores = row.get("briers") or {}
            if name not in scores or "baseline" not in scores:
                continue
            score = float(scores[name])
            baseline = float(scores["baseline"])
            briers.append(score)
            if score < baseline - max(float(epsilon), 1e-12):
                wins += 1
        count = len(briers)
        fraction = (wins / count) if count else 0.0
        mean_brier = float(np.mean(briers)) if briers else None
        models[name] = {
            "mean_brier": mean_brier,
            "std_brier": float(np.std(briers)) if briers else None,
            "win_fraction": fraction,
            "n_folds": count,
        }
        if (
            mean_brier is not None
            and fraction + 1e-12 >= float(min_win_fraction)
            and mean_brier < best_mean
        ):
            best_mean = mean_brier
            selected = name
    return {
        "folds_total": len(rows),
        "folds_eligible": len(eligible),
        "min_test": int(min_test),
        "metrics_kind": "calibrated",
        "baseline_kind": "calibration_frequency",
        "models": models,
        "selected_pipeline": selected,
    }


def score_fold_briers(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    X_test: pd.DataFrame,
    y_test: pd.Series,
    *,
    direction: str,
    X_val: pd.DataFrame | None = None,
    y_val: pd.Series | None = None,
    X_cal: pd.DataFrame | None = None,
    y_cal: pd.Series | None = None,
    seq_train=None,
    seq_val=None,
    seq_cal=None,
    seq_test=None,
    include_heavy_models: bool = True,
) -> dict[str, float]:
    """Score independently calibrated candidates on an untouched fold test.

    A missing, undersized, or single-class calibration slice makes the fold
    unavailable. Test labels are used only to compute final Brier scores.
    """
    from app.ml.baseline import LogisticRegressionBaseline, VolatilityDistanceBaseline
    from app.ml.candidate_selection import (
        calibrated_candidate_predictions,
        fit_candidate_calibrators,
    )

    y_tr = np.asarray(y_train, dtype=float).ravel()
    if (
        len(X_train) < 20 or len(X_train) != len(y_tr)
        or not np.isfinite(y_tr).all() or not np.isin(y_tr, [0.0, 1.0]).all()
        or len(np.unique(y_tr)) < 2
    ):
        return {}
    if X_cal is None or y_cal is None or len(X_cal) != len(y_cal):
        return {}
    y_ca = np.asarray(y_cal, dtype=float).ravel()
    if (
        len(y_ca) < 11 or not np.isfinite(y_ca).all()
        or not np.isin(y_ca, [0.0, 1.0]).all() or len(np.unique(y_ca)) < 2
    ):
        return {}
    if len(X_test) != len(y_test) or len(y_test) < 5:
        return {}

    rate = float(np.mean(y_tr))
    raw_cal = {"baseline": np.full(len(X_cal), rate)}
    raw_test = {"baseline": np.full(len(X_test), rate)}

    def probabilities(values, length: int) -> np.ndarray:
        values = np.asarray(values, dtype=float).ravel()
        if len(values) != length or not np.isfinite(values).all():
            raise ValueError("Fold predictions are not aligned or finite")
        if ((values < 0) | (values > 1)).any():
            raise ValueError("Fold predictions must be probabilities")
        return values

    def predict_candidate(name: str, model) -> None:
        cal_predictions = probabilities(model.predict_proba(X_cal), len(X_cal))
        test_predictions = probabilities(model.predict_proba(X_test), len(X_test))
        raw_cal[name] = cal_predictions
        raw_test[name] = test_predictions

    for name, model in (
        ("logistic_regression", LogisticRegressionBaseline()),
        ("volatility_distance", VolatilityDistanceBaseline()),
    ):
        try:
            model.fit(X_train, y_train)
            predict_candidate(name, model)
        except Exception:
            pass

    has_val = (
        X_val is not None and y_val is not None
        and len(X_val) > 0 and len(X_val) == len(y_val)
    )
    X_es, y_es = (X_val, y_val) if has_val else (None, None)
    if include_heavy_models:
        try:
            from app.ml.xgboost_model import XGBoostTouchModel

            model = XGBoostTouchModel(direction=direction, n_estimators=80, early_stopping_rounds=10)
            model.fit(X_train, y_train, X_es, y_es)
            predict_candidate("xgboost", model)
        except Exception:
            pass

        try:
            from app.ml.catboost_model import CatBoostTouchModel

            model = CatBoostTouchModel(direction=direction, iterations=80, early_stopping_rounds=10)
            model.fit(X_train, y_train, X_es, y_es)
            predict_candidate("catboost", model)
        except Exception:
            pass

        has_sequences = all(
            seq is not None and len(seq) == len(frame)
            for seq, frame in ((seq_train, X_train), (seq_cal, X_cal), (seq_test, X_test))
        )
        if has_sequences:
            try:
                from app.ml.lstm_model import LSTMTouchModel

                model = LSTMTouchModel(direction=direction, max_epochs=3, patience=2)
                use_seq_val = has_val and seq_val is not None and len(seq_val) == len(X_val)
                model.fit(
                    seq_train,
                    y_tr,
                    seq_val if use_seq_val else None,
                    np.asarray(y_val, dtype=float) if use_seq_val else None,
                )
                cal_predictions = probabilities(model.predict_proba(seq_cal), len(X_cal))
                test_predictions = probabilities(model.predict_proba(seq_test), len(X_test))
                raw_cal["lstm"] = cal_predictions
                raw_test["lstm"] = test_predictions
            except Exception:
                pass

    calibrators = fit_candidate_calibrators(raw_cal, y_ca)
    calibrated = calibrated_candidate_predictions(
        {name: values for name, values in raw_test.items() if name in calibrators},
        calibrators,
    )
    if "baseline" not in calibrated:
        return {}
    scores: dict[str, float] = {}
    for name, predictions in calibrated.items():
        score = _brier(y_test, predictions)
        if score is not None:
            scores[name] = score
    return scores


def evaluate_outer_folds(
    features: pd.DataFrame,
    labels: pd.Series,
    times: pd.Series,
    sequences: np.ndarray,
    *,
    direction: str,
    outcome_window_seconds: int,
    gap_seconds: int,
    n_splits: int = 5,
    include_heavy_models: bool = True,
    lockbox_fraction: float | None = None,
) -> tuple[dict, int]:
    """Score expanding purged folds on the pre-lockbox development prefix."""
    fraction = 0.12 if lockbox_fraction is None else float(lockbox_fraction)
    lock_start = lockbox_start_index(len(features), fraction=fraction)
    dev_n = lock_start
    rows: list[dict] = []
    seq_by_time = {
        pd.Timestamp(t): sequences[i]
        for i, t in enumerate(times)
        if i < len(sequences)
    }
    for test_start, test_end in outer_test_windows(dev_n, n_splits=n_splits):
        _test_start = test_start
        test_n = int(test_end) - int(_test_start)
        prefix_n = int(test_end)
        if prefix_n <= 30 or test_n < 5:
            continue
        test_frac = test_n / prefix_n
        pre = 1.0 - test_frac
        try:
            fold = chronological_split(
                features.iloc[:prefix_n].reset_index(drop=True),
                labels.iloc[:prefix_n].reset_index(drop=True),
                times.iloc[:prefix_n].reset_index(drop=True),
                train_frac=pre * 0.70,
                val_frac=pre * 0.15,
                cal_frac=pre * 0.15,
                test_frac=test_frac,
                outcome_window_seconds=outcome_window_seconds,
                gap_seconds=gap_seconds,
            )
        except ValueError:
            continue
        if len(fold.X_train) < 20 or len(fold.X_test) < 5:
            continue
        prep = fit_feature_prep(fold.X_train)
        X_train = apply_feature_prep(fold.X_train, prep)
        X_val = apply_feature_prep(fold.X_val, prep)
        X_cal = apply_feature_prep(fold.X_cal, prep)
        X_test = apply_feature_prep(fold.X_test, prep)
        seq_train = _sequences_for(fold.train_times, seq_by_time, sequences.shape[-2] if sequences.ndim == 3 else 0)
        seq_val = _sequences_for(fold.val_times, seq_by_time, sequences.shape[-2] if sequences.ndim == 3 else 0)
        seq_cal = _sequences_for(fold.cal_times, seq_by_time, sequences.shape[-2] if sequences.ndim == 3 else 0)
        seq_test = _sequences_for(fold.test_times, seq_by_time, sequences.shape[-2] if sequences.ndim == 3 else 0)
        if any(
            len(seq) != len(frame)
            for seq, frame in ((seq_train, X_train), (seq_val, X_val), (seq_cal, X_cal), (seq_test, X_test))
        ):
            seq_train = None
            seq_val = None
            seq_cal = None
            seq_test = None
        briers = score_fold_briers(
            X_train,
            fold.y_train,
            X_test,
            fold.y_test,
            direction=direction,
            X_val=X_val,
            y_val=fold.y_val,
            X_cal=X_cal,
            y_cal=fold.y_cal,
            seq_train=seq_train,
            seq_val=seq_val,
            seq_cal=seq_cal,
            seq_test=seq_test,
            include_heavy_models=include_heavy_models,
        )
        if "baseline" not in briers:
            continue
        rows.append({"n_test": int(len(X_test)), "n_cal": int(len(X_cal)), "briers": briers})
    return aggregate_walk_forward(rows), lock_start


def _sequences_for(times: pd.Series, seq_by_time: dict, seq_len: int) -> np.ndarray:
    mats = []
    for stamp in times:
        key = pd.Timestamp(stamp)
        if key in seq_by_time:
            mats.append(seq_by_time[key])
    if not mats:
        width = 4
        return np.zeros((0, max(seq_len, 1), width), dtype=np.float32)
    return np.stack(mats, axis=0)
