"""Temperature scaling fitted only on the calibration partition."""

from __future__ import annotations

import numpy as np
from scipy.optimize import minimize_scalar

from app.digitmatch.probability import as_simplex, log_loss_safe


def apply_temperature(probabilities: np.ndarray, temperature: float) -> np.ndarray:
    if not np.isfinite(temperature) or temperature <= 0:
        raise ValueError("temperature must be a positive finite number")
    probs = np.clip(np.asarray(probabilities, dtype=float), 1e-12, 1)
    logits = np.log(probs)
    scaled = logits / float(temperature)
    scaled = scaled - scaled.max(axis=1, keepdims=True)
    exp = np.exp(scaled)
    return as_simplex(exp / exp.sum(axis=1, keepdims=True))


def fit_temperature(probabilities: np.ndarray, y_true: np.ndarray) -> float:
    """Minimize multiclass log loss. y_true must be the calibration partition only."""

    def objective(temperature: float) -> float:
        return log_loss_safe(y_true, apply_temperature(probabilities, temperature))

    result = minimize_scalar(objective, bounds=(0.05, 10.0), method="bounded")
    if not result.success:
        return 1.0
    return float(result.x)
