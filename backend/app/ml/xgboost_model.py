"""
XGBoost challenger model — optional, used only if CatBoost comparison is useful.
"""

import numpy as np
import pandas as pd
from xgboost import XGBClassifier
from sklearn.preprocessing import StandardScaler
import pickle
import structlog
from typing import Optional
import os

logger = structlog.get_logger(__name__)


class XGBoostTouchModel:
    """
    XGBoost gradient-boosted model as a challenger to CatBoost.
    Only used if comparison adds value — not the default.
    """

    name = "xgboost_touch"

    def __init__(
        self,
        direction: str = "upper",
        n_estimators: int = 500,
        learning_rate: float = 0.05,
        max_depth: int = 6,
        reg_lambda: float = 1.0,
        early_stopping_rounds: int = 50,
        random_state: int = 42,
    ):
        self.direction = direction
        self.hyperparams = {
            "n_estimators": n_estimators,
            "learning_rate": learning_rate,
            "max_depth": max_depth,
            "reg_lambda": reg_lambda,
            "early_stopping_rounds": early_stopping_rounds,
            "random_state": random_state,
        }
        self.scaler = StandardScaler()
        self.model: Optional[XGBClassifier] = None
        self._feature_names: list[str] = []
        self._is_fitted = False

    def fit(
        self,
        X_train: pd.DataFrame,
        y_train: pd.Series,
        X_val: pd.DataFrame = None,
        y_val: pd.Series = None,
    ):
        X_train_clean = X_train.replace([np.inf, -np.inf], np.nan).fillna(0)
        X_train_scaled = self.scaler.fit_transform(X_train_clean)
        self._feature_names = list(X_train.columns)

        from app.ml.device import xgboost_device_kwargs

        has_val = X_val is not None and y_val is not None
        device_kwargs = xgboost_device_kwargs()
        clf_kwargs = {
            "n_estimators": self.hyperparams["n_estimators"],
            "learning_rate": self.hyperparams["learning_rate"],
            "max_depth": self.hyperparams["max_depth"],
            "reg_lambda": self.hyperparams["reg_lambda"],
            # Keep the probability target at the observed event frequency;
            # inverse-frequency weights instead target a balanced population.
            "objective": "binary:logistic",
            "eval_metric": "logloss",
            "random_state": self.hyperparams["random_state"],
            **device_kwargs,
        }
        # early_stopping_rounds belongs on the estimator (sklearn API), not fit(callbacks=...)
        if has_val:
            clf_kwargs["early_stopping_rounds"] = self.hyperparams["early_stopping_rounds"]

        fit_kwargs = {"verbose": False}
        if has_val:
            X_val_clean = X_val.replace([np.inf, -np.inf], np.nan).fillna(0)
            X_val_scaled = self.scaler.transform(X_val_clean)
            fit_kwargs["eval_set"] = [(X_val_scaled, y_val)]

        try:
            self.model = XGBClassifier(**clf_kwargs)
            self.model.fit(X_train_scaled, y_train, **fit_kwargs)
            used = device_kwargs.get("device", "cpu")
        except Exception as gpu_err:
            if not device_kwargs:
                raise
            logger.warning("xgboost_gpu_failed_fallback_cpu", error=str(gpu_err))
            for key in ("device", "tree_method"):
                clf_kwargs.pop(key, None)
            self.model = XGBClassifier(**clf_kwargs)
            self.model.fit(X_train_scaled, y_train, **fit_kwargs)
            used = "cpu"
        self._is_fitted = True

        logger.info("xgboost_fitted",
                     direction=self.direction,
                     n_features=len(self._feature_names),
                     n_train=len(X_train),
                     device=used)

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        if not self._is_fitted:
            raise RuntimeError("Model not fitted")
        from app.features.pipeline import align_features_to_model

        X_clean = align_features_to_model(X, self._feature_names)
        X_scaled = self.scaler.transform(X_clean)
        return self.model.predict_proba(X_scaled)[:, 1]

    def feature_importance(self) -> pd.DataFrame:
        if not self._is_fitted:
            return pd.DataFrame()
        importances = self.model.feature_importances_
        return pd.DataFrame({
            "feature": self._feature_names,
            "importance": importances,
        }).sort_values("importance", ascending=False)

    def save(self, path: str):
        if self.model:
            os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
            data = {
                "model": self.model,
                "scaler": self.scaler,
                "features": self._feature_names,
            }
            with open(path, "wb") as f:
                pickle.dump(data, f)

    def load(self, path: str):
        with open(path, "rb") as f:
            data = pickle.load(f)
        self.model = data["model"]
        self.scaler = data["scaler"]
        self._feature_names = data["features"]
        self._is_fitted = True
