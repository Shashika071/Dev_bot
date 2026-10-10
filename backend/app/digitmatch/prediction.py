"""Live probabilities from the active bundle. No result is invented when no model is active."""

from __future__ import annotations

import numpy as np

from app.digitmatch.calibration import apply_temperature
from app.digitmatch.features import LOOKBACK, build_examples
from app.digitmatch.probability import as_simplex
from app.digitmatch.registry import load_bundle
from app.digitmatch.training import _predict_matrix


def predict_latest(bundle_path: str, digits: np.ndarray, prices: np.ndarray, usable: np.ndarray) -> np.ndarray | None:
    if len(digits) < LOOKBACK:
        return None
    tail = slice(-(LOOKBACK + 5), None)
    examples = build_examples(digits[tail], prices[tail], usable[tail])
    if len(examples["index"]) == 0:
        return None
    if int(examples["index"][-1]) != len(digits[tail]) - 1 - int(examples["horizon"]):
        return None
    bundle = load_bundle(bundle_path)
    name = bundle["selected_model"]
    last = {
        "X": examples["X"][-1:],
        "y": examples["y"][-1:],
        "index": np.asarray([int(examples["index"][-1])]),
    }
    full_digits = digits[tail]
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
            ensemble["weight_left"] * _predict_matrix(left, examples, left_x, full_digits)
            + (1.0 - ensemble["weight_left"]) * _predict_matrix(right, examples, right_x, full_digits)
        )
        return probs[0]
    model = bundle["models"][name]
    part = last
    if model.kind == "ml":
        part = dict(last)
        part["X"] = bundle["selector"].transform(last["X"])
    raw = _predict_matrix(model, examples, part, full_digits)
    return apply_temperature(raw, 1.0)[0] if model.temperature == 1 else raw[0]
