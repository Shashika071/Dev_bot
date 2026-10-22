"""Chronological metrics. None means the number was not estimated."""
from __future__ import annotations

import math

import numpy as np
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

from app.synthetic.ml import wilson_lower


def clip_probability(probability) -> np.ndarray:
    return np.clip(np.asarray(probability, dtype=float), 1e-6, 1 - 1e-6)


def classification_metrics(labels, probability) -> dict:
    actual = np.asarray(labels, dtype=int)
    score = clip_probability(probability)
    if len(actual) != len(score) or not len(actual) or not np.isfinite(score).all():
        return {"accuracy": None, "auc": None, "log_loss": None, "brier": None, "rows": int(len(actual))}
    predicted = (score >= 0.5).astype(int)
    metrics = {
        "accuracy": float((predicted == actual).mean()),
        "log_loss": float(log_loss(actual, score, labels=[0, 1])),
        "brier": float(brier_score_loss(actual, score)),
        "rows": int(len(actual)),
        "auc": None,
    }
    if len(set(actual.tolist())) == 2:
        metrics["auc"] = float(roc_auc_score(actual, score))
    return metrics


def wilson_interval(hits: int, total: int) -> tuple[float | None, float | None]:
    lower = wilson_lower(hits, total)
    upper = None if wilson_lower(total - hits, total) is None else 1 - wilson_lower(total - hits, total)
    return lower, upper


def max_drawdown(profits: list[float]) -> float | None:
    if not profits:
        return None
    equity = peak = worst = 0.0
    for profit in profits:
        if profit is None or not math.isfinite(profit):
            return None
        equity += profit
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
    return float(worst)


def expected_value_bound(win_probability: float | None, payout: float | None) -> float | None:
    if win_probability is None or payout is None:
        return None
    return float(win_probability * payout - (1 - win_probability))
