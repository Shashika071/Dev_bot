"""Live probabilities from the active bundle. No result is invented when no model is active."""

from __future__ import annotations

import numpy as np

from app.digitmatch.features import LOOKBACK, feature_names, live_feature_row
from app.digitmatch.probability import as_simplex
from app.digitmatch.registry import load_bundle
from app.digitmatch.training import _predict_matrix


def _needs_features(model) -> bool:
    return getattr(model, "kind", "") == "ml"


def probabilities_for(bundle: dict, digits: np.ndarray, prices: np.ndarray, usable: np.ndarray) -> np.ndarray | None:
    """Score the latest tick. A transition or frequency model does not need the next five digits."""
    digits = np.asarray(digits)
    prices = np.asarray(prices, dtype=float)
    usable = np.asarray(usable, dtype=bool)
    if len(digits) == 0 or not bool(usable[-1]):
        return None
    name = bundle["selected_model"]
    row = live_feature_row(digits, prices, usable) if len(digits) >= LOOKBACK else None
    if name == "ensemble":
        ensemble = bundle["ensemble"]
        left = bundle["models"][ensemble["left"]]
        right = bundle["models"][ensemble["right"]]
        if row is None and (_needs_features(left) or _needs_features(right)):
            return None
    else:
        model = bundle["models"][name]
        if row is None and _needs_features(model):
            return None
    if row is None:
        row = np.zeros(len(feature_names()), dtype=np.float32)
    index = len(digits) - 1
    last = {
        "X": np.asarray(row, dtype=np.float32).reshape(1, -1),
        "y": np.asarray([int(digits[index])]),
        "index": np.asarray([index]),
    }
    if name == "ensemble":
        ensemble = bundle["ensemble"]
        left = bundle["models"][ensemble["left"]]
        right = bundle["models"][ensemble["right"]]
        left_x = last
        right_x = last
        if left.kind == "ml":
            left_x = dict(last)
            left_x["X"] = bundle["selector"].transform(last["X"])
        if right.kind == "ml":
            right_x = dict(last)
            right_x["X"] = bundle["selector"].transform(last["X"])
        probs = as_simplex(
            ensemble["weight_left"] * _predict_matrix(left, {}, left_x, digits)
            + (1.0 - ensemble["weight_left"]) * _predict_matrix(right, {}, right_x, digits)
        )
        return probs[0]
    model = bundle["models"][name]
    part = last
    if model.kind == "ml":
        part = dict(last)
        part["X"] = bundle["selector"].transform(last["X"])
    return _predict_matrix(model, {}, part, digits)[0]


def predict_latest(bundle_path: str, digits: np.ndarray, prices: np.ndarray, usable: np.ndarray) -> np.ndarray | None:
    return probabilities_for(load_bundle(bundle_path), digits, prices, usable)
