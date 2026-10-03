"""
Ensemble methods for combining XGBoost, CatBoost, and LSTM probabilities.

A) Weighted average — non-negative weights summing to 1 (includes equal weights)
B) Stacking — regularised logistic regression on chronological OOF base predictions
"""

from __future__ import annotations

import pickle
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss
import structlog

logger = structlog.get_logger(__name__)

MODEL_KEYS = ("xgboost", "catboost", "lstm")


@dataclass
class WeightedEnsemble:
    weights: dict[str, float]

    def predict(self, preds: dict[str, np.ndarray]) -> np.ndarray:
        total = None
        for k, w in self.weights.items():
            p = np.asarray(preds[k], dtype=float).ravel()
            total = w * p if total is None else total + w * p
        return total

    def save(self, path: str):
        with open(path, "wb") as f:
            pickle.dump({"weights": self.weights}, f)

    @classmethod
    def load(cls, path: str) -> "WeightedEnsemble":
        with open(path, "rb") as f:
            data = pickle.load(f)
        return cls(weights=data["weights"])


@dataclass
class StackingEnsemble:
    meta: LogisticRegression

    def predict(self, preds: dict[str, np.ndarray]) -> np.ndarray:
        X = np.column_stack([np.asarray(preds[k], dtype=float).ravel() for k in MODEL_KEYS])
        return self.meta.predict_proba(X)[:, 1]

    def save(self, path: str):
        with open(path, "wb") as f:
            pickle.dump({"meta": self.meta, "keys": list(MODEL_KEYS)}, f)

    @classmethod
    def load(cls, path: str) -> "StackingEnsemble":
        with open(path, "rb") as f:
            data = pickle.load(f)
        return cls(meta=data["meta"])


def equal_weights() -> dict[str, float]:
    w = 1.0 / len(MODEL_KEYS)
    return {k: w for k in MODEL_KEYS}


def fit_weighted_ensemble(
    val_preds: dict[str, np.ndarray],
    y_val: np.ndarray,
    grid_step: float = 0.1,
) -> WeightedEnsemble:
    """
    Choose non-negative weights summing to 1 using validation Brier score only.
    Always includes equal weighting as a candidate.
    """
    y = np.asarray(y_val, dtype=float).ravel()
    matrices = {k: np.asarray(val_preds[k], dtype=float).ravel() for k in MODEL_KEYS}
    candidates = [equal_weights()]

    # Coarse simplex grid for 3 weights
    steps = np.arange(0.0, 1.0 + 1e-9, grid_step)
    for w0 in steps:
        for w1 in steps:
            w2 = 1.0 - w0 - w1
            if w2 < -1e-9:
                continue
            if w2 > 1.0 + 1e-9:
                continue
            w2 = max(0.0, w2)
            s = w0 + w1 + w2
            if s <= 0:
                continue
            candidates.append(
                {
                    "xgboost": w0 / s,
                    "catboost": w1 / s,
                    "lstm": w2 / s,
                }
            )

    best_w = equal_weights()
    best_score = float("inf")
    for w in candidates:
        blended = sum(w[k] * matrices[k] for k in MODEL_KEYS)
        score = brier_score_loss(y, np.clip(blended, 1e-6, 1 - 1e-6))
        if score < best_score:
            best_score = score
            best_w = w

    logger.info("weighted_ensemble_fit", weights=best_w, val_brier=best_score)
    return WeightedEnsemble(weights=best_w)


def chronological_oof_predictions(
    train_fn: Callable[[np.ndarray, np.ndarray], object],
    predict_fn: Callable[[object, np.ndarray], np.ndarray],
    X: np.ndarray | object,
    y: np.ndarray,
    n_folds: int = 5,
    min_train: int = 80,
) -> np.ndarray:
    """
    Expanding-window chronological OOF predictions.
    Fold k trains on earlier rows only and predicts the next block.
    """
    y = np.asarray(y, dtype=float).ravel()
    n = len(y)
    oof = np.full(n, np.nan, dtype=float)
    if n < min_train + 10:
        return oof

    fold_size = max(1, (n - min_train) // n_folds)
    for fold in range(n_folds):
        train_end = min_train + fold * fold_size
        test_end = n if fold == n_folds - 1 else min(train_end + fold_size, n)
        if train_end < min_train or test_end <= train_end:
            continue
        X_tr = X[:train_end]
        y_tr = y[:train_end]
        X_te = X[train_end:test_end]
        model = train_fn(X_tr, y_tr)
        oof[train_end:test_end] = predict_fn(model, X_te)

    return oof


def fit_stacking_ensemble(
    oof_preds: dict[str, np.ndarray],
    y: np.ndarray,
    C: float = 1.0,
) -> StackingEnsemble:
    """
    Fit meta-learner on chronological OOF base predictions only.
    Rows with any NaN OOF prediction are dropped.
    """
    y = np.asarray(y, dtype=float).ravel()
    X = np.column_stack([np.asarray(oof_preds[k], dtype=float).ravel() for k in MODEL_KEYS])
    mask = ~np.isnan(X).any(axis=1)
    if mask.sum() < 20:
        raise ValueError(f"Insufficient OOF rows for stacking: {int(mask.sum())}")

    meta = LogisticRegression(
        C=C,
        penalty="l2",
        solver="lbfgs",
        max_iter=1000,
        class_weight="balanced",
    )
    meta.fit(X[mask], y[mask])
    logger.info("stacking_ensemble_fit", n_oof=int(mask.sum()))
    return StackingEnsemble(meta=meta)


def blend_predictions(
    method: str,
    preds: dict[str, np.ndarray],
    weighted: Optional[WeightedEnsemble] = None,
    stacking: Optional[StackingEnsemble] = None,
) -> np.ndarray:
    if method == "weighted":
        if weighted is None:
            raise ValueError("weighted ensemble required")
        return weighted.predict(preds)
    if method == "stacking":
        if stacking is None:
            raise ValueError("stacking ensemble required")
        return stacking.predict(preds)
    if method in preds:
        return np.asarray(preds[method], dtype=float).ravel()
    raise ValueError(f"Unknown blend method: {method}")
