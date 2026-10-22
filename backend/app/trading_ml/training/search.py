"""Pick model settings on an early training slice. Later rows stay unseen."""
from __future__ import annotations

import numpy as np
from sklearn.metrics import log_loss

from app.trading_ml.evaluation.metrics import clip_probability
from app.trading_ml.models.direction import fit_estimator, positive_probability


def candidate_params(name: str) -> list[dict]:
    if name == "logistic":
        return [{"C": 0.2}, {"C": 0.5}, {"C": 1.0}]
    if name in {"xgboost", "lightgbm"}:
        return [
            {"n_estimators": 30, "max_depth": 2, "learning_rate": 0.08},
            {"n_estimators": 40, "max_depth": 3, "learning_rate": 0.08},
        ]
    if name == "catboost":
        return [
            {"iterations": 30, "depth": 3, "learning_rate": 0.08},
            {"iterations": 40, "depth": 4, "learning_rate": 0.08},
        ]
    return []


def search_training_params(name: str, values: np.ndarray, labels: np.ndarray) -> dict:
    """Return the settings with the lowest log loss on the tail of this training array."""
    labels = np.asarray(labels, dtype=int)
    values = np.asarray(values, dtype=float)
    cut = int(len(labels) * 0.8)
    if cut < 30 or len(labels) - cut < 15 or len(set(labels[:cut].tolist())) < 2:
        return {}
    best: dict = {}
    best_loss = None
    for params in candidate_params(name):
        try:
            model = fit_estimator(name, values[:cut], labels[:cut], params)
            probability = positive_probability(model, values[cut:])
            loss = float(log_loss(labels[cut:], clip_probability(probability), labels=[0, 1]))
        except Exception:
            continue
        if best_loss is None or loss < best_loss:
            best = dict(params)
            best_loss = loss
    return best
