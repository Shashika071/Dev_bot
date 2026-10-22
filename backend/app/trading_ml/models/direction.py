"""Tabular direction models. Each model is fit only on its training rows."""
from __future__ import annotations

import numpy as np

CATALOG = ("logistic", "xgboost", "lightgbm", "catboost")


def resolve_models(requested: list[str] | None = None) -> tuple[list[str], list[str]]:
    wanted = list(requested or CATALOG)
    unknown = [name for name in wanted if name not in CATALOG]
    if unknown:
        raise ValueError(f"Unknown direction model: {', '.join(unknown)}")
    available = []
    notes = []
    for name in wanted:
        if name == "logistic":
            available.append(name)
            continue
        module = {"xgboost": "xgboost", "lightgbm": "lightgbm", "catboost": "catboost"}[name]
        try:
            __import__(module)
        except ImportError:
            notes.append(f"{name} is not installed, so it was not scored and no result was invented.")
            continue
        available.append(name)
    if not available:
        raise ValueError("No direction model is available to train")
    return available, notes


def fit_estimator(name: str, values: np.ndarray, labels: np.ndarray, params: dict | None = None):
    labels = np.asarray(labels, dtype=int)
    if len(set(labels.tolist())) < 2:
        raise ValueError(f"{name} needs both rising and falling outcomes")
    chosen = dict(params or {})
    if name == "logistic":
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import StandardScaler

        model = Pipeline([
            ("scaler", StandardScaler()),
            ("logistic", LogisticRegression(max_iter=400, C=float(chosen.get("C", 0.5)), random_state=41)),
        ])
    elif name == "xgboost":
        from xgboost import XGBClassifier

        model = XGBClassifier(
            n_estimators=int(chosen.get("n_estimators", 40)),
            max_depth=int(chosen.get("max_depth", 3)),
            learning_rate=float(chosen.get("learning_rate", 0.08)),
            objective="binary:logistic", eval_metric="logloss",
            random_state=41, n_jobs=1, verbosity=0,
        )
    elif name == "lightgbm":
        from lightgbm import LGBMClassifier

        model = LGBMClassifier(
            n_estimators=int(chosen.get("n_estimators", 40)),
            max_depth=int(chosen.get("max_depth", 3)),
            learning_rate=float(chosen.get("learning_rate", 0.08)),
            objective="binary", random_state=41, n_jobs=1, verbose=-1,
        )
    elif name == "catboost":
        from catboost import CatBoostClassifier

        model = CatBoostClassifier(
            iterations=int(chosen.get("iterations", 40)),
            depth=int(chosen.get("depth", 4)),
            learning_rate=float(chosen.get("learning_rate", 0.08)),
            loss_function="Logloss",
            random_seed=41, thread_count=1, verbose=False, allow_writing_files=False,
        )
    else:
        raise ValueError(f"Unknown direction model: {name}")
    model.fit(values, labels)
    return model


def positive_probability(model, values: np.ndarray) -> np.ndarray:
    raw = np.asarray(model.predict_proba(values), dtype=float)
    classes = [int(label) for label in model.classes_]
    if 1 not in classes or raw.ndim != 2 or raw.shape[0] != len(values):
        raise ValueError("Direction model did not return a probability for a rise")
    probability = raw[:, classes.index(1)]
    if not np.isfinite(probability).all() or (probability < 0).any() or (probability > 1).any():
        raise ValueError("Direction model returned a probability outside 0 to 1")
    return probability
