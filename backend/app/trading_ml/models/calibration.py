"""Held-out probability calibration. An unfitted calibrator does not invent a map."""
from __future__ import annotations

import numpy as np
from sklearn.linear_model import LogisticRegression


class PlattCalibrator:
    def __init__(self, min_samples: int = 30):
        self.min_samples = min_samples
        self._model: LogisticRegression | None = None
        self.fitted = False
        self.method = "platt"

    def fit(self, probability, labels) -> "PlattCalibrator":
        raw = np.clip(np.asarray(probability, dtype=float), 1e-6, 1 - 1e-6)
        target = np.asarray(labels, dtype=int)
        self.fitted = False
        self._model = None
        if len(raw) != len(target) or len(raw) < self.min_samples or len(set(target.tolist())) < 2:
            return self
        if not np.isfinite(raw).all():
            return self
        logit = np.log(raw / (1 - raw)).reshape(-1, 1)
        self._model = LogisticRegression(max_iter=200)
        self._model.fit(logit, target)
        self.fitted = True
        return self

    def transform(self, probability) -> np.ndarray:
        raw = np.clip(np.asarray(probability, dtype=float), 1e-6, 1 - 1e-6)
        if not self.fitted or self._model is None:
            return raw
        logit = np.log(raw / (1 - raw)).reshape(-1, 1)
        return self._model.predict_proba(logit)[:, list(self._model.classes_).index(1)]
