"""Baselines and optional ML candidates. A baseline is allowed to win."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.neural_network import MLPClassifier
from sklearn.preprocessing import StandardScaler

from app.digitmatch.probability import as_simplex, map_estimator_proba, uniform

try:
    from xgboost import XGBClassifier
except Exception:  # pragma: no cover - environment without the wheel
    XGBClassifier = None


LSTM_ENABLED = False


@dataclass
class FittedModel:
    name: str
    kind: str
    temperature: float = 1.0
    estimator: object | None = None
    scaler: StandardScaler | None = None
    frequency: np.ndarray | None = None
    lag_counts: np.ndarray | None = None
    rolling_window: int = 200
    rolling_alpha: float = 1.0

    def raw_probabilities(self, features: np.ndarray, digits_at_decision: np.ndarray, history_digits: np.ndarray, history_index: np.ndarray) -> np.ndarray:
        n = len(features)
        if self.kind == "uniform":
            return uniform(n)
        if self.kind == "frequency":
            return np.repeat(self.frequency, n, axis=0)
        if self.kind == "rolling":
            return _rolling_probabilities(history_digits, history_index, self.rolling_window, self.rolling_alpha)
        if self.kind == "transition":
            current = np.asarray(digits_at_decision, dtype=int)
            probs = self.lag_counts[current]
            return as_simplex(probs)
        assert self.estimator is not None and self.scaler is not None
        transformed = self.scaler.transform(features)
        return map_estimator_proba(self.estimator, transformed)


def _rolling_probabilities(history_digits: np.ndarray, decision_index: np.ndarray, window: int, alpha: float) -> np.ndarray:
    digits = np.asarray(history_digits, dtype=int)
    one_hot = np.eye(10)[digits]
    cumulative = np.cumsum(one_hot, axis=0)
    rows = []
    for index in decision_index:
        i = int(index)
        start = i - window
        counts = cumulative[i] - (cumulative[start] if start >= 0 else 0)
        used = window if start >= 0 else i + 1
        rows.append((counts + alpha) / (used + 10 * alpha))
    return as_simplex(np.vstack(rows))


def fit_frequency(train_digits: np.ndarray) -> FittedModel:
    counts = np.bincount(np.asarray(train_digits, dtype=int), minlength=10).astype(float)
    counts = np.clip(counts, 1e-6, None)
    freq = counts / counts.sum()
    return FittedModel(name="frequency", kind="frequency", frequency=freq.reshape(1, 10))


def fit_rolling() -> FittedModel:
    return FittedModel(name="rolling_frequency", kind="rolling")


def fit_transition(decision_index: np.ndarray, digits: np.ndarray, horizon: int = 5) -> FittedModel:
    """Laplace-smoothed P(digit at t+horizon | digit at t), training rows only."""
    counts = np.ones((10, 10), dtype=float)
    for index in np.asarray(decision_index, dtype=int):
        current = int(digits[index])
        nxt = int(digits[index + horizon])
        counts[current, nxt] += 1.0
    probs = counts / counts.sum(axis=1, keepdims=True)
    return FittedModel(name="transition", kind="transition", lag_counts=probs)


def fit_logistic(features: np.ndarray, y: np.ndarray, seed: int) -> FittedModel:
    scaler = StandardScaler()
    transformed = scaler.fit_transform(features)
    model = LogisticRegression(C=0.5, max_iter=400, solver="lbfgs", random_state=seed)
    model.fit(transformed, y)
    return FittedModel(name="logistic", kind="ml", estimator=model, scaler=scaler)


def fit_xgboost(features: np.ndarray, y: np.ndarray, seed: int, *, n_estimators: int = 80) -> FittedModel | None:
    if XGBClassifier is None:
        return None
    scaler = StandardScaler()
    transformed = scaler.fit_transform(features)
    model = XGBClassifier(
        objective="multi:softprob",
        num_class=10,
        n_estimators=n_estimators,
        max_depth=3,
        learning_rate=0.08,
        reg_lambda=1.0,
        subsample=0.8,
        colsample_bytree=0.8,
        random_state=seed,
        n_jobs=1,
        tree_method="hist",
    )
    model.fit(transformed, y)
    return FittedModel(name="xgboost", kind="ml", estimator=model, scaler=scaler)


def fit_mlp(features: np.ndarray, y: np.ndarray, seed: int) -> FittedModel:
    scaler = StandardScaler()
    transformed = scaler.fit_transform(features)
    model = MLPClassifier(
        hidden_layer_sizes=(32,),
        max_iter=80,
        random_state=seed,
        early_stopping=True,
        validation_fraction=0.15,
    )
    model.fit(transformed, y)
    return FittedModel(name="mlp", kind="ml", estimator=model, scaler=scaler)


def uniform_model() -> FittedModel:
    return FittedModel(name="uniform", kind="uniform")


def assert_lstm_disabled() -> None:
    if LSTM_ENABLED:
        raise RuntimeError("LSTM must stay disabled until a separate experiment enables it")
