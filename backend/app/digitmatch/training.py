"""Train on chronological data and leave the final test untouched until selection is frozen."""

from __future__ import annotations

import hashlib
import gc
import json
import platform
from importlib.metadata import version as pkg_version

import numpy as np
from sklearn.feature_selection import VarianceThreshold

from app.digitmatch import FEATURE_SCHEMA, TARGET_NOTE
from app.digitmatch.calibration import apply_temperature, fit_temperature
from app.digitmatch.evaluation import evaluate_predictions, simulate_assumed_payout
from app.digitmatch.modeling import (
    FittedModel,
    assert_lstm_disabled,
    fit_frequency,
    fit_logistic,
    fit_mlp,
    fit_rolling,
    fit_transition,
    fit_xgboost,
    uniform_model,
)
from app.digitmatch.probability import as_simplex, log_loss_safe
from app.digitmatch.splits import SplitFractions, chronological_split, rows_for


def dependency_versions() -> dict[str, str]:
    out = {"python": platform.python_version()}
    for name in ("numpy", "pandas", "scikit-learn", "xgboost", "scipy"):
        try:
            out[name] = pkg_version(name)
        except Exception:
            out[name] = "unavailable"
    return out


def _slice(examples: dict, decisions: np.ndarray) -> dict:
    rows = rows_for(examples["index"], decisions)
    return {
        "rows": rows,
        "X": examples["X"][rows],
        "y": examples["y"][rows],
        "index": examples["index"][rows],
    }


def _predict_matrix(model: FittedModel, examples: dict, part: dict, digits: np.ndarray) -> np.ndarray:
    raw = model.raw_probabilities(
        part["X"],
        digits[part["index"]],
        digits,
        part["index"],
    )
    return apply_temperature(raw, model.temperature)


def select_best(scores: dict[str, float]) -> str:
    return min(scores, key=scores.get)


def learn_ensemble(candidates: dict[str, np.ndarray], y: np.ndarray) -> dict | None:
    """Weights are chosen on the selection partition only. No equal-weight default."""
    if len(candidates) < 2 or len(y) == 0:
        return None
    ranking = sorted(candidates, key=lambda name: log_loss_safe(y, candidates[name]))
    left, right = ranking[0], ranking[1]
    best_single = log_loss_safe(y, candidates[left])
    chosen = None
    for step in range(5):
        weight = step / 4
        blended = as_simplex(weight * candidates[left] + (1 - weight) * candidates[right])
        loss = log_loss_safe(y, blended)
        if loss + 1e-9 < best_single and (chosen is None or loss < chosen["log_loss"]):
            chosen = {
                "left": left,
                "right": right,
                "weight_left": weight,
                "log_loss": loss,
            }
    return chosen


def select_margin_with_quotes(
    probabilities: np.ndarray,
    y: np.ndarray,
    ask: np.ndarray,
    payout: np.ndarray,
    grid: tuple[float, ...],
) -> float | None:
    """Pick a margin using selection-set quotes. Returns None when quotes are absent."""
    if ask is None or payout is None:
        return None
    best_margin = None
    best_pnl = None
    for margin in grid:
        pnl = 0.0
        for row in range(len(y)):
            choice = int(np.argmax(probabilities[row]))
            probability = float(probabilities[row, choice])
            break_even = float(ask[row]) / float(payout[row])
            if probability < break_even + margin:
                continue
            pnl += (float(payout[row]) - float(ask[row])) if choice == int(y[row]) else -float(ask[row])
        if best_pnl is None or pnl > best_pnl:
            best_pnl = pnl
            best_margin = float(margin)
    return best_margin


def walk_forward(digits: np.ndarray, examples: dict, pretest: np.ndarray, seed: int) -> list[dict]:
    """Expanding folds on pre-test rows only."""
    part = _slice(examples, np.sort(pretest))
    n = len(part["y"])
    if n < 30:
        return []
    folds = []
    edges = [n // 3, 2 * n // 3, n]
    start = 0
    for fold, edge in enumerate(edges):
        if fold == 0:
            start = edge
            continue
        train = {
            "X": part["X"][:start],
            "y": part["y"][:start],
            "index": part["index"][:start],
        }
        valid = {
            "X": part["X"][start:edge],
            "y": part["y"][start:edge],
            "index": part["index"][start:edge],
        }
        if len(train["y"]) < 10 or len(valid["y"]) < 5:
            start = edge
            continue
        model = fit_logistic(train["X"], train["y"], seed)
        model.temperature = 1.0
        probs = _predict_matrix(model, examples, valid, digits)
        folds.append(
            {
                "fold": fold,
                "train_rows": int(len(train["y"])),
                "validation_rows": int(len(valid["y"])),
                "log_loss": log_loss_safe(valid["y"], probs),
                "model": "logistic",
            }
        )
        start = edge
    return folds


def run_training(
    *,
    digits: np.ndarray,
    examples: dict,
    epochs: np.ndarray,
    seed: int = 42,
    fractions: SplitFractions | None = None,
    enable_mlp: bool = False,
    enable_xgboost: bool = True,
    xgb_estimators: int = 80,
    default_margin: float = 0.02,
    margin_grid: tuple[float, ...] = (0.0, 0.01, 0.02, 0.05),
    selection_ask: np.ndarray | None = None,
    selection_payout: np.ndarray | None = None,
    assumed_ask: float | None = None,
    assumed_payout: float | None = None,
) -> dict:
    assert_lstm_disabled()
    fractions = fractions or SplitFractions()
    examples["X"] = np.ascontiguousarray(examples["X"], dtype=np.float32)
    split = chronological_split(examples["index"], fractions=fractions, horizon=int(examples["horizon"]))
    train = _slice(examples, split["train"])
    calibration = _slice(examples, split["calibration"])
    selection = _slice(examples, split["selection"])
    test = _slice(examples, split["test"])
    if min(len(train["y"]), len(calibration["y"]), len(selection["y"]), len(test["y"])) == 0:
        raise ValueError("a chronological partition is empty after purging boundary labels")

    selector = VarianceThreshold(threshold=0.0)
    selector.fit(train["X"])
    kept = selector.get_support()

    def transform(part: dict) -> dict:
        copied = dict(part)
        copied["X"] = selector.transform(part["X"])
        return copied

    train_x = transform(train)
    calibration_x = transform(calibration)
    selection_x = transform(selection)
    test_x = transform(test)
    train.pop("X", None)
    gc.collect()

    models: dict[str, FittedModel] = {
        "uniform": uniform_model(),
        "frequency": fit_frequency(train["y"]),
        "rolling_frequency": fit_rolling(),
        "transition": fit_transition(train["index"], digits, horizon=int(examples["horizon"])),
    }
    failures = []
    try:
        models["logistic"] = fit_logistic(train_x["X"], train["y"], seed)
    except Exception as exc:
        failures.append({"name": "logistic", "error": str(exc)})
    if enable_xgboost:
        try:
            fitted = fit_xgboost(train_x["X"], train["y"], seed, n_estimators=xgb_estimators)
            if fitted is not None:
                models["xgboost"] = fitted
        except Exception as exc:
            failures.append({"name": "xgboost", "error": str(exc)})
    if enable_mlp:
        try:
            models["mlp"] = fit_mlp(train_x["X"], train["y"], seed)
        except Exception as exc:
            failures.append({"name": "mlp", "error": str(exc)})

    for model in models.values():
        if model.kind == "ml":
            cal_probs = _predict_matrix(model, examples, calibration_x, digits)
        else:
            cal_probs = _predict_matrix(model, examples, calibration, digits)
        model.temperature = fit_temperature(cal_probs, calibration["y"])

    selection_probs = {}
    selection_scores = {}
    for name, model in models.items():
        part = selection_x if model.kind == "ml" else selection
        probs = _predict_matrix(model, examples, part, digits)
        selection_probs[name] = probs
        selection_scores[name] = log_loss_safe(selection["y"], probs)

    ensemble = learn_ensemble(selection_probs, selection["y"])
    chosen_name = select_best(selection_scores)
    chosen_probs = selection_probs[chosen_name]
    if ensemble is not None:
        blended = as_simplex(
            ensemble["weight_left"] * selection_probs[ensemble["left"]]
            + (1 - ensemble["weight_left"]) * selection_probs[ensemble["right"]]
        )
        selection_scores["ensemble"] = ensemble["log_loss"]
        if ensemble["log_loss"] + 1e-9 < selection_scores[chosen_name]:
            chosen_name = "ensemble"
            chosen_probs = blended

    # Margin is frozen here, before the test labels are read.
    optimized_margin = select_margin_with_quotes(
        chosen_probs, selection["y"], selection_ask, selection_payout, margin_grid
    )
    margin = default_margin if optimized_margin is None else optimized_margin
    margin_source = (
        "configured_default_no_historical_payout_quotes"
        if optimized_margin is None
        else "selection_partition_quotes"
    )

    pretest = np.concatenate([split["train"], split["calibration"], split["selection"]])
    for part in (calibration, selection, train_x, calibration_x, selection_x):
        part.pop("X", None)
    gc.collect()
    folds = walk_forward(digits, examples, pretest, seed)

    # Final test is evaluated once, after the choices above are fixed.
    if chosen_name == "ensemble":
        left = models[ensemble["left"]]
        right = models[ensemble["right"]]
        left_part = test_x if left.kind == "ml" else test
        right_part = test_x if right.kind == "ml" else test
        test_probs = as_simplex(
            ensemble["weight_left"] * _predict_matrix(left, examples, left_part, digits)
            + (1 - ensemble["weight_left"]) * _predict_matrix(right, examples, right_part, digits)
        )
    else:
        model = models[chosen_name]
        part = test_x if model.kind == "ml" else test
        test_probs = _predict_matrix(model, examples, part, digits)

    test_epochs = epochs[test["index"]] if epochs is not None else None
    test_report = evaluate_predictions(test["y"], test_probs, epochs=test_epochs, seed=seed)
    uniform_probs = np.full_like(test_probs, 0.1)
    uniform_loss = log_loss_safe(test["y"], uniform_probs)
    interval = test_report["log_loss_minus_uniform"]
    if interval["low"] is None:
        comparison = "inconclusive"
    elif interval["high"] < 0:
        comparison = "better_log_loss_than_uniform_on_this_test"
    elif interval["low"] > 0:
        comparison = "worse_than_uniform_baseline"
    else:
        comparison = "inconclusive"

    assumed = None
    if assumed_ask is not None and assumed_payout is not None:
        assumed = simulate_assumed_payout(
            test["y"], test_probs, ask=assumed_ask, total_payout=assumed_payout, margin=margin
        )

    first = int(examples["index"][0]) if len(examples["index"]) else 0
    last = int(examples["index"][-1]) if len(examples["index"]) else 0
    dataset_version = hashlib.sha256(
        f"{FEATURE_SCHEMA}:{len(digits)}:{first}:{last}:{seed}".encode()
    ).hexdigest()[:16]

    return {
        "schema": FEATURE_SCHEMA,
        "target_note": TARGET_NOTE,
        "seed": seed,
        "dataset_version": dataset_version,
        "dependency_versions": dependency_versions(),
        "calibration_method": "temperature_scaling",
        "selected_model": chosen_name,
        "selection_log_loss": selection_scores,
        "ensemble": ensemble,
        "margin": margin,
        "margin_source": margin_source,
        "promoted": False,
        "walk_forward": folds,
        "split_counts": {
            "train": int(len(train["y"])),
            "calibration": int(len(calibration["y"])),
            "selection": int(len(selection["y"])),
            "test": int(len(test["y"])),
            "purged": int(len(split["purged"])),
        },
        "features_kept": int(kept.sum()),
        "failures": failures,
        "final_test": test_report,
        "comparison_to_uniform": comparison,
        "uniform_test_log_loss": uniform_loss,
        "has_demonstrated_edge": False,
        "edge_statement": (
            "No payout-adjusted edge is claimed. The test comparison is a research "
            f"log-loss result ({comparison}) and is not a trading-profit result."
        ),
        "assumed_payout_simulation": assumed,
        "models": models,
        "selector": selector,
        "time_start": int(epochs[examples["index"][0]]) if len(examples["index"]) else None,
        "time_end": int(epochs[examples["index"][-1]]) if len(examples["index"]) else None,
    }
