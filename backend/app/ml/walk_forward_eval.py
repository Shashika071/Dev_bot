"""Purged expanding-window evaluation helpers.

These functions choose windows and summarize fold scores. Model fitting stays
with the caller so the same split rules can be tested without training.
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
            if score <= baseline - float(epsilon):
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
    seq_train=None,
    seq_test=None,
    include_heavy_models: bool = True,
) -> dict[str, float]:
    """Retrain candidates on one purged fold and score the fold test once."""
    from app.ml.baseline import LogisticRegressionBaseline, VolatilityDistanceBaseline

    y_tr = np.asarray(y_train, dtype=float).ravel()
    if len(np.unique(y_tr)) < 2 or len(X_train) < 20:
        return {}
    scores: dict[str, float] = {}
    rate = float(np.mean(y_tr))
    baseline = _brier(y_test, np.full(len(y_test), rate))
    if baseline is None:
        return {}
    scores["baseline"] = baseline

    logistic = LogisticRegressionBaseline()
    logistic.fit(X_train, y_train)
    logistic_score = _brier(y_test, logistic.predict_proba(X_test))
    if logistic_score is not None:
        scores["logistic_regression"] = logistic_score

    volatility = VolatilityDistanceBaseline()
    volatility.fit(X_train, y_train)
    vol_score = _brier(y_test, volatility.predict_proba(X_test))
    if vol_score is not None:
        scores["volatility_distance"] = vol_score

    if not include_heavy_models:
        return scores

    es = max(8, len(X_train) // 10)
    if len(X_train) - es >= 15:
        X_fit, y_fit = X_train.iloc[:-es], y_train.iloc[:-es]
        X_es, y_es = X_train.iloc[-es:], y_train.iloc[-es:]
    else:
        X_fit, y_fit, X_es, y_es = X_train, y_train, None, None

    try:
        from app.ml.xgboost_model import XGBoostTouchModel

        model = XGBoostTouchModel(direction=direction, n_estimators=80, early_stopping_rounds=10)
        model.fit(X_fit, y_fit, X_es, y_es)
        score = _brier(y_test, model.predict_proba(X_test))
        if score is not None:
            scores["xgboost"] = score
    except Exception:
        pass

    try:
        from app.ml.catboost_model import CatBoostTouchModel

        model = CatBoostTouchModel(direction=direction, iterations=80, early_stopping_rounds=10)
        model.fit(X_fit, y_fit, X_es, y_es)
        score = _brier(y_test, model.predict_proba(X_test))
        if score is not None:
            scores["catboost"] = score
    except Exception:
        pass

    if seq_train is not None and seq_test is not None and len(seq_train) == len(X_train):
        try:
            from app.ml.lstm_model import LSTMTouchModel

            model = LSTMTouchModel(direction=direction, max_epochs=3, patience=2)
            seq_fit = seq_train if X_es is None else seq_train[:-es]
            seq_es = None if X_es is None else seq_train[-es:]
            y_fit_np = np.asarray(y_fit, dtype=float)
            y_es_np = None if y_es is None else np.asarray(y_es, dtype=float)
            model.fit(seq_fit, y_fit_np, seq_es, y_es_np)
            score = _brier(y_test, model.predict_proba(seq_test))
            if score is not None:
                scores["lstm"] = score
        except Exception:
            pass
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
) -> tuple[dict, int]:
    """Score expanding purged folds on the pre-lockbox development prefix."""
    lock_start = lockbox_start_index(len(features))
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
        X_test = apply_feature_prep(fold.X_test, prep)
        seq_train = _sequences_for(fold.train_times, seq_by_time, sequences.shape[-2] if sequences.ndim == 3 else 0)
        seq_test = _sequences_for(fold.test_times, seq_by_time, sequences.shape[-2] if sequences.ndim == 3 else 0)
        if len(seq_train) != len(X_train):
            seq_train = None
            seq_test = None
        briers = score_fold_briers(
            X_train,
            fold.y_train,
            X_test,
            fold.y_test,
            direction=direction,
            seq_train=seq_train,
            seq_test=seq_test,
            include_heavy_models=include_heavy_models,
        )
        if "baseline" not in briers:
            continue
        rows.append({"n_test": int(len(X_test)), "briers": briers})
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
