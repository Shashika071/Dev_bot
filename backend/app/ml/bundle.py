"""
Saved multi-model pipeline bundle for inference.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

import numpy as np
import pandas as pd
import structlog

from app.ml.calibration import ProbabilityCalibrator
from app.ml.catboost_model import CatBoostTouchModel
from app.ml.ensemble import (
    MODEL_KEYS,
    StackingEnsemble,
    WeightedEnsemble,
    blend_predictions,
)
from app.ml.lstm_model import LSTMTouchModel
from app.ml.xgboost_model import XGBoostTouchModel

logger = structlog.get_logger(__name__)


class ModelPipelineBundle:
    """Holds base models + selected ensemble + calibrator for one direction."""

    def __init__(self, metadata: dict):
        self.metadata = metadata
        self.selected = metadata.get("selected_pipeline", "catboost")
        self.xgboost: Optional[XGBoostTouchModel] = None
        self.catboost: Optional[CatBoostTouchModel] = None
        self.lstm: Optional[LSTMTouchModel] = None
        self.weighted: Optional[WeightedEnsemble] = None
        self.stacking: Optional[StackingEnsemble] = None
        self.logistic = None
        self.volatility = None
        self.calibrator: Optional[ProbabilityCalibrator] = None
        self.baseline_rate: float = float(metadata.get("baseline_touch_rate", 0.5))

    @classmethod
    def load(cls, metadata: dict) -> "ModelPipelineBundle":
        from app.ml.artifacts import verify_artifact_checksums
        from app.ml.baseline import LogisticRegressionBaseline, VolatilityDistanceBaseline

        verify_artifact_checksums(metadata)
        bundle = cls(metadata)
        paths = dict(metadata.get("paths") or {})
        direction = metadata.get("direction", "upper")

        # Backward compatible: older metas only stored model_path / calibrator_path
        legacy_model = metadata.get("model_path")
        if legacy_model and not paths.get("catboost") and str(legacy_model).endswith(".cbm"):
            paths["catboost"] = legacy_model
        if metadata.get("calibrator_path") and not paths.get("calibrator"):
            paths["calibrator"] = metadata["calibrator_path"]

        if paths.get("xgboost") and os.path.exists(paths["xgboost"]):
            m = XGBoostTouchModel(direction=direction)
            m.load(paths["xgboost"])
            bundle.xgboost = m

        if paths.get("catboost") and os.path.exists(paths["catboost"]):
            m = CatBoostTouchModel(direction=direction)
            m.load(paths["catboost"])
            # CatBoost save_model does not store feature names; recover from metadata
            if not m._feature_names and metadata.get("feature_columns"):
                m._feature_names = list(metadata["feature_columns"])
            bundle.catboost = m

        if paths.get("lstm") and os.path.exists(paths["lstm"]):
            m = LSTMTouchModel(direction=direction)
            m.load(paths["lstm"])
            bundle.lstm = m

        if paths.get("weighted") and os.path.exists(paths["weighted"]):
            bundle.weighted = WeightedEnsemble.load(paths["weighted"])

        if paths.get("stacking") and os.path.exists(paths["stacking"]):
            bundle.stacking = StackingEnsemble.load(paths["stacking"])

        if paths.get("logistic") and os.path.exists(paths["logistic"]):
            logistic = LogisticRegressionBaseline()
            logistic.load(paths["logistic"])
            bundle.logistic = logistic

        if paths.get("volatility") and os.path.exists(paths["volatility"]):
            volatility = VolatilityDistanceBaseline()
            volatility.load(paths["volatility"])
            bundle.volatility = volatility

        if paths.get("calibrator") and os.path.exists(paths["calibrator"]):
            cal = ProbabilityCalibrator()
            cal.load(paths["calibrator"])
            bundle.calibrator = cal

        # A missing selected model is a failed load, not a silent algorithm change.
        required = {
            "xgboost": bundle.xgboost,
            "catboost": bundle.catboost,
            "lstm": bundle.lstm,
            "weighted": bundle.weighted,
            "stacking": bundle.stacking,
            "logistic_regression": bundle.logistic,
            "volatility_distance": bundle.volatility,
        }
        if bundle.selected in required and required[bundle.selected] is None:
            raise RuntimeError(f"Selected model {bundle.selected} is missing or unreadable")

        return bundle

    def predict_components(
        self,
        features: pd.DataFrame,
        sequences: Optional[np.ndarray] = None,
    ) -> dict[str, float]:
        from app.features.pipeline import align_features_to_model, apply_feature_prep

        meta_cols = list(self.metadata.get("feature_columns") or [])
        prep = self.metadata.get("feature_prep")
        frame = features
        if prep:
            frame = apply_feature_prep(
                features,
                prep,
                strict=bool(self.metadata.get("strict_features")),
            )
            meta_cols = list(prep.get("feature_columns") or meta_cols)
        aligned = align_features_to_model(
            frame,
            meta_cols,
            fill_values=(prep or {}).get("fill_values"),
            strict=bool(self.metadata.get("strict_features")) and not prep,
        ) if meta_cols else frame

        out: dict[str, float] = {"baseline": self.baseline_rate}
        if self.xgboost is not None:
            out["xgboost"] = float(self.xgboost.predict_proba(aligned)[0])
        if self.catboost is not None:
            if not self.catboost._feature_names and meta_cols:
                self.catboost._feature_names = list(meta_cols)
            out["catboost"] = float(self.catboost.predict_proba(aligned)[0])
        if self.lstm is not None and sequences is not None and len(sequences):
            out["lstm"] = float(self.lstm.predict_proba(sequences)[0])
        if self.logistic is not None:
            out["logistic_regression"] = float(self.logistic.predict_proba(aligned)[0])
        if self.volatility is not None:
            out["volatility_distance"] = float(self.volatility.predict_proba(aligned)[0])
        return out

    def predict_raw(
        self,
        features: pd.DataFrame,
        sequences: Optional[np.ndarray] = None,
    ) -> tuple[float, dict[str, float]]:
        comps = self.predict_components(features, sequences)
        selected = self.selected

        if selected == "baseline":
            return self.baseline_rate, comps

        if selected in ("logistic_regression", "volatility_distance", "xgboost", "catboost", "lstm"):
            if selected not in comps:
                raise RuntimeError(f"Selected model {selected} missing at inference")
            return comps[selected], comps

        if selected in ("weighted", "stacking"):
            if selected == "weighted":
                if self.weighted is None:
                    raise RuntimeError("Selected weighted ensemble is missing")
                needed = [key for key, weight in self.weighted.weights.items() if float(weight) > 0]
            else:
                if self.stacking is None:
                    raise RuntimeError("Selected stacking ensemble is missing")
                needed = list(MODEL_KEYS)
            missing = [key for key in needed if key not in comps]
            if missing:
                raise RuntimeError(f"Selected ensemble is missing components: {', '.join(missing)}")
            preds = {key: np.array([comps[key]]) for key in MODEL_KEYS if key in comps}
            raw = float(
                blend_predictions(
                    selected,
                    preds,
                    weighted=self.weighted,
                    stacking=self.stacking,
                )[0]
            )
            return raw, comps

        raise RuntimeError(f"Unknown selected pipeline: {selected}")

    def predict_calibrated(
        self,
        features: pd.DataFrame,
        sequences: Optional[np.ndarray] = None,
    ) -> dict[str, Any]:
        raw, comps = self.predict_raw(features, sequences)
        if self.calibrator is not None and self.calibrator.is_fitted:
            cal = float(self.calibrator.calibrate(np.array([raw]))[0])
            stats = self.calibrator.empirical_stats(cal, band=0.05)
        else:
            cal = raw
            stats = {"sample_count": 0, "hit_rate": 0.0, "ci_lower": 0.0, "ci_upper": 0.0}
        return {
            "raw_probability": raw,
            "calibrated_probability": cal,
            "component_probabilities": comps,
            "selected_pipeline": self.selected,
            "empirical_stats": stats,
        }
