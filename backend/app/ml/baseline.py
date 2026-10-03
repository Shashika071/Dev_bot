"""
Baseline models: historical touch frequency and logistic regression.
These establish the minimum performance bar for ML models to beat.
"""

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
import pickle
import structlog
from typing import Optional

logger = structlog.get_logger(__name__)


class HistoricalFrequencyBaseline:
    """
    Baseline D: Predicts touch probability based on historical frequency
    conditional on direction, barrier distance, duration, and recent volatility.
    """

    name = "historical_frequency"

    def __init__(self):
        self._frequencies: dict = {}
        self._overall_rate: float = 0.5

    def fit(self, labels_df: pd.DataFrame, group_columns: list[str] = None):
        """
        Compute conditional touch frequencies from labeled data.
        """
        if group_columns is None:
            group_columns = ["barrier_direction"]

        self._overall_rate = labels_df["touched"].mean()

        for col_combo in [group_columns]:
            key = tuple(col_combo)
            valid_cols = [c for c in col_combo if c in labels_df.columns]
            if valid_cols:
                grouped = labels_df.groupby(valid_cols)["touched"].agg(["mean", "count"])
                self._frequencies[key] = grouped.to_dict("index")

        logger.info("baseline_frequency_fitted",
                     overall_rate=self._overall_rate,
                     groups=len(self._frequencies))

    def predict_proba(self, direction: str, **conditions) -> float:
        """Predict touch probability based on historical frequency."""
        # Try to find matching group
        for key, freq_dict in self._frequencies.items():
            lookup = tuple(conditions.get(k, direction) for k in key)
            if lookup in freq_dict:
                return freq_dict[lookup]["mean"]

        return self._overall_rate


class LogisticRegressionBaseline:
    """
    Baseline D: Simple logistic regression model.
    Fitted with StandardScaler on training data only.
    """

    name = "logistic_regression"

    def __init__(self, C: float = 1.0, max_iter: int = 1000):
        self.pipeline = Pipeline([
            ("scaler", StandardScaler()),
            ("model", LogisticRegression(
                C=C,
                max_iter=max_iter,
                solver="lbfgs",
                class_weight="balanced",
            ))
        ])
        self._is_fitted = False
        self._feature_names: list[str] = []

    def fit(self, X_train: pd.DataFrame, y_train: pd.Series):
        """Fit on training data. Scaler is fit here only."""
        self._feature_names = list(X_train.columns)

        # Handle NaN/Inf
        X_clean = X_train.replace([np.inf, -np.inf], np.nan).fillna(0)

        self.pipeline.fit(X_clean, y_train)
        self._is_fitted = True

        logger.info("logistic_baseline_fitted",
                     n_samples=len(X_train),
                     n_features=len(self._feature_names))

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        """Predict touch probability."""
        if not self._is_fitted:
            raise RuntimeError("Model not fitted")
        X_clean = X.replace([np.inf, -np.inf], np.nan).fillna(0)
        return self.pipeline.predict_proba(X_clean)[:, 1]

    def save(self, path: str):
        with open(path, "wb") as f:
            pickle.dump({"pipeline": self.pipeline, "features": self._feature_names}, f)

    def load(self, path: str):
        with open(path, "rb") as f:
            data = pickle.load(f)
        self.pipeline = data["pipeline"]
        self._feature_names = data["features"]
        self._is_fitted = True
