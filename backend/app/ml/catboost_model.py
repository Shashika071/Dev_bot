"""
CatBoost model for touch probability prediction.
Primary ML model — starts simple and grows with data.
"""

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier, Pool
import structlog
from typing import Optional
import os

logger = structlog.get_logger(__name__)


class CatBoostTouchModel:
    """
    CatBoost gradient-boosted model for predicting touch probability.

    Separate models are trained for upper and lower touch directions,
    or a direction-conditioned model can be used.
    """

    name = "catboost_touch"

    def __init__(
        self,
        direction: str = "upper",
        iterations: int = 500,
        learning_rate: float = 0.05,
        depth: int = 6,
        l2_leaf_reg: float = 3.0,
        early_stopping_rounds: int = 50,
        random_seed: int = 42,
    ):
        self.direction = direction
        self.hyperparams = {
            "iterations": iterations,
            "learning_rate": learning_rate,
            "depth": depth,
            "l2_leaf_reg": l2_leaf_reg,
            "early_stopping_rounds": early_stopping_rounds,
            "random_seed": random_seed,
        }
        self.model: Optional[CatBoostClassifier] = None
        self._feature_names: list[str] = []
        self._is_fitted = False

    def fit(
        self,
        X_train: pd.DataFrame,
        y_train: pd.Series,
        X_val: pd.DataFrame = None,
        y_val: pd.Series = None,
    ):
        """
        Train CatBoost model with early stopping on validation set.
        """
        self._feature_names = list(X_train.columns)

        # Handle NaN/Inf
        X_train_clean = X_train.replace([np.inf, -np.inf], np.nan)
        train_pool = Pool(X_train_clean, label=y_train)

        eval_set = None
        if X_val is not None and y_val is not None:
            X_val_clean = X_val.replace([np.inf, -np.inf], np.nan)
            eval_set = Pool(X_val_clean, label=y_val)

        self.model = CatBoostClassifier(
            iterations=self.hyperparams["iterations"],
            learning_rate=self.hyperparams["learning_rate"],
            depth=self.hyperparams["depth"],
            l2_leaf_reg=self.hyperparams["l2_leaf_reg"],
            eval_metric="Logloss",
            use_best_model=True if eval_set else False,
            random_seed=self.hyperparams["random_seed"],
            verbose=100,
            nan_mode="Min",
            auto_class_weights="Balanced",
        )

        self.model.fit(
            train_pool,
            eval_set=eval_set,
            early_stopping_rounds=self.hyperparams["early_stopping_rounds"] if eval_set else None,
        )
        self._is_fitted = True

        best_iter = self.model.get_best_iteration() if eval_set else self.hyperparams["iterations"]
        logger.info("catboost_fitted",
                     direction=self.direction,
                     best_iteration=best_iter,
                     n_features=len(self._feature_names),
                     n_train=len(X_train))

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        """Predict touch probability (uncalibrated)."""
        if not self._is_fitted:
            raise RuntimeError("Model not fitted")
        from app.features.pipeline import align_features_to_model

        X_clean = align_features_to_model(X, self._feature_names or None)
        return self.model.predict_proba(X_clean)[:, 1]

    def feature_importance(self) -> pd.DataFrame:
        """Get feature importances."""
        if not self._is_fitted:
            return pd.DataFrame()

        importances = self.model.get_feature_importance()
        return pd.DataFrame({
            "feature": self._feature_names,
            "importance": importances,
        }).sort_values("importance", ascending=False)

    def save(self, path: str):
        """Save model to file."""
        if self.model:
            os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
            self.model.save_model(path)
            logger.info("catboost_saved", path=path)

    def load(self, path: str):
        """Load model from file."""
        self.model = CatBoostClassifier()
        self.model.load_model(path)
        self._is_fitted = True
        logger.info("catboost_loaded", path=path)
