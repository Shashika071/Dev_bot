"""Probability helpers. Every distribution is a 10-class simplex."""

from __future__ import annotations

import numpy as np

CLASSES = 10


class ProbabilityError(ValueError):
    pass


def as_simplex(probabilities: np.ndarray) -> np.ndarray:
    values = np.asarray(probabilities, dtype=float)
    if values.ndim == 1:
        values = values.reshape(1, -1)
    if values.shape[1] != CLASSES:
        raise ProbabilityError(f"expected {CLASSES} probabilities, got {values.shape[1]}")
    if not np.isfinite(values).all():
        raise ProbabilityError("probabilities must be finite")
    if (values < 0).any():
        raise ProbabilityError("probabilities must be non-negative")
    totals = values.sum(axis=1, keepdims=True)
    if np.any(totals <= 0):
        raise ProbabilityError("probabilities must have a positive sum")
    return values / totals


def map_estimator_proba(estimator, features: np.ndarray) -> np.ndarray:
    """Align sklearn/xgboost columns onto digits 0..9, then repair the simplex."""
    raw = np.asarray(estimator.predict_proba(features), dtype=float)
    classes = [int(c) for c in getattr(estimator, "classes_")]
    full = np.zeros((raw.shape[0], CLASSES), dtype=float)
    for column, label in enumerate(classes):
        if 0 <= label <= 9:
            full[:, label] = raw[:, column]
    # A missing class would otherwise be a hard zero and break log loss.
    full = np.clip(full, 1e-12, None)
    return as_simplex(full)


def uniform(n_rows: int) -> np.ndarray:
    return np.full((n_rows, CLASSES), 1.0 / CLASSES, dtype=float)


def log_loss_safe(y_true: np.ndarray, probabilities: np.ndarray) -> float:
    probs = as_simplex(probabilities)
    y = np.asarray(y_true, dtype=int)
    picked = probs[np.arange(len(y)), y]
    return float(-np.mean(np.log(np.clip(picked, 1e-15, 1))))


def multiclass_brier(y_true: np.ndarray, probabilities: np.ndarray) -> float:
    probs = as_simplex(probabilities)
    y = np.asarray(y_true, dtype=int)
    one_hot = np.eye(CLASSES)[y]
    return float(np.mean(np.sum((probs - one_hot) ** 2, axis=1)))
