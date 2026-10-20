"""EURUSD Rise/Fall labels, causality, purged split, and hybrid ML gate."""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from app.forex.ml.dataset import (
    FEATURE_COLUMNS,
    STEP_15M,
    STEP_4H,
    build_dataset,
    features_from_live_frames,
    label_outcome,
    label_prices,
)
from app.forex.ml.predict import ml_blocks_entry, ml_status, predict_frames, score_idea
from app.forex.ml.train import (
    apply_wait_policy,
    artifact_paths,
    purged_time_split,
)


NOW = 1_792_396_800


def history(step: int, *, direction: int = 1, count: int = 200) -> list[dict]:
    end = NOW // step * step
    rows = []
    for index in range(count):
        close = 1.1 + direction * (index * 0.00015 + 0.00065 * math.sin(index * 0.7))
        open_ = close - direction * 0.0001
        rows.append({
            "epoch": end - (count - index) * step,
            "open": open_, "close": close,
            "high": max(open_, close) + 0.00015,
            "low": min(open_, close) - 0.00015,
        })
    return rows


def frames(direction: int = 1) -> dict:
    return {
        "15m": history(900, direction=direction, count=220),
        "1h": history(3600, direction=direction, count=120),
        "4h": history(14400, direction=direction, count=90),
    }


class _FixedProba:
    def __init__(self, p_rise: float):
        self.p_rise = p_rise

    def predict_proba(self, X):
        n = len(X)
        p = self.p_rise
        return np.asarray([[1.0 - p, p]] * n, dtype=float)


def _install_model(model_base: Path, p_rise: float = 0.9, *, promoted: bool = True) -> None:
    paths = artifact_paths(model_base)
    paths["root"].mkdir(parents=True, exist_ok=True)
    joblib.dump({"pipeline": _FixedProba(p_rise), "features": FEATURE_COLUMNS}, paths["model"])
    paths["meta"].write_text(json.dumps({
        "symbol": "frxEURUSD", "threshold": 0.55, "metrics": {"samples": 10},
        "trained_at": NOW, "features": FEATURE_COLUMNS, "promoted": promoted,
    }), encoding="utf-8")


def test_label_outcome_rise_fall_and_tie_is_loss():
    step = 900
    m15 = history(step, count=40)
    entry = int(m15[10]["epoch"]) + 5
    after = [row for row in m15 if row["epoch"] <= entry < row["epoch"] + step]
    assert after
    entry_price = float(after[0]["open"])
    expiry = entry + 14400
    priced = label_prices(pd.DataFrame(m15), entry, expiry)
    assert priced is not None
    _entry, exit_price, meta = priced
    assert meta["exit_close_lag"] <= step
    # Force clear rise on the exit candle.
    exit_rows = [row for row in m15 if row["epoch"] == meta["exit_bar_epoch"]]
    exit_rows[0]["close"] = entry_price + 0.01
    frame = pd.DataFrame(m15)
    assert label_outcome(frame, entry, expiry, "RISE") == "win"
    assert label_outcome(frame, entry, expiry, "FALL") == "loss"
    exit_rows[0]["close"] = entry_price
    frame = pd.DataFrame(m15)
    assert label_outcome(frame, entry, expiry, "RISE") == "loss"


def test_label_rejects_large_exit_gap():
    m15 = pd.DataFrame(history(900, count=20))
    entry = int(m15.iloc[5]["epoch"]) + 5
    # Expiry far beyond available closes.
    assert label_prices(m15, entry, entry + 900 * 1000) is None


def test_h4_features_ignore_still_forming_bars():
    data = frames()
    baseline = features_from_live_frames(data, now=NOW)
    assert baseline is not None
    polluted = copy.deepcopy(data)
    # Append a still-forming H4 bar whose close is after NOW.
    open_epoch = NOW - (NOW % STEP_4H)
    if open_epoch + STEP_4H <= NOW:
        open_epoch = NOW - 60
    polluted["4h"].append({
        "epoch": open_epoch, "open": 1.2, "high": 100.0, "low": 1.0, "close": 99.0,
    })
    forming = [row for row in polluted["4h"] if int(row["epoch"]) <= NOW < int(row["epoch"]) + STEP_4H]
    assert forming
    assert features_from_live_frames(polluted, now=NOW) == baseline


def test_features_and_labels_ignore_future_bars():
    data = frames()
    baseline = features_from_live_frames(data, now=NOW)
    assert baseline is not None
    polluted = copy.deepcopy(data)
    for name, step in (("15m", 900), ("1h", 3600), ("4h", 14400)):
        polluted[name].append({
            "epoch": NOW + 10 * step, "open": 9, "high": 99, "low": 0.01, "close": 50,
        })
    assert features_from_live_frames(polluted, now=NOW) == baseline

    ds_base = build_dataset(data["15m"], data["1h"], data["4h"], sample_every=8)
    ds_polluted = build_dataset(polluted["15m"], polluted["1h"], polluted["4h"], sample_every=8)
    assert not ds_base.empty
    shared = ds_polluted[ds_polluted["decision_epoch"].isin(set(ds_base["decision_epoch"]))].reset_index(drop=True)
    assert list(shared["decision_epoch"]) == list(ds_base["decision_epoch"])
    assert list(shared["label_rise"]) == list(ds_base["label_rise"])
    for col in FEATURE_COLUMNS:
        assert np.allclose(shared[col], ds_base[col], equal_nan=True)


def test_purged_split_removes_label_overlap():
    data = frames()
    dataset = build_dataset(data["15m"], data["1h"], data["4h"], sample_every=2)
    assert len(dataset) > 40
    train, test = purged_time_split(dataset, test_fraction=0.25)
    assert int(train["expiry_epoch"].max()) <= int(test["decision_epoch"].min())


def test_wait_policy_metrics_differ_from_forced_binary():
    proba = np.asarray([0.9, 0.5, 0.1, 0.52])
    y = np.asarray([1, 1, 0, 0])
    metrics = apply_wait_policy(proba, y, threshold=0.55)
    assert metrics["selected"] == 2
    assert metrics["wait_rate"] == 0.5
    assert metrics["forced_binary_accuracy"] != metrics["selected_accuracy"]


def test_ml_inactive_without_artifact(tmp_path: Path):
    status = ml_status(tmp_path / "models")
    assert status["ready"] is False
    assert status["installed"] is False
    view = predict_frames(tmp_path / "models", frames(), now=NOW, checklist_direction="RISE")
    assert view["hybrid_passed"] is None
    assert not ml_blocks_entry(view)


def test_hybrid_gate_requires_direction_agreement(tmp_path: Path):
    model_base = tmp_path / "models"
    _install_model(model_base, p_rise=0.92)
    agree = predict_frames(model_base, frames(1), now=NOW, checklist_direction="RISE")
    assert agree["ready"] is True
    assert agree["direction"] == "RISE"
    assert agree["hybrid_passed"] is True

    disagree = predict_frames(model_base, frames(1), now=NOW, checklist_direction="FALL")
    assert disagree["hybrid_passed"] is False
    assert ml_blocks_entry(disagree)

    _install_model(model_base, p_rise=0.5)
    unsure = predict_frames(model_base, frames(1), now=NOW, checklist_direction="RISE")
    assert unsure["direction"] == "WAIT"
    assert unsure["hybrid_passed"] is False


def test_unpromoted_candidate_is_an_idea_and_does_not_trade(tmp_path: Path):
    model_base = tmp_path / "models"
    paths = artifact_paths(model_base)
    folder = paths["candidates"] / "20261020T120000Z"
    folder.mkdir(parents=True)
    joblib.dump({"pipeline": _FixedProba(0.71), "features": FEATURE_COLUMNS}, folder / "model.joblib")
    (folder / "meta.json").write_text(json.dumps({
        "symbol": "frxEURUSD", "threshold": 0.55, "features": FEATURE_COLUMNS,
        "promoted": False, "metrics": {"selected_accuracy": 0.51, "test_auc": 0.54},
    }), encoding="utf-8")
    view = predict_frames(model_base, frames(1), now=NOW, checklist_direction="RISE")
    assert view["installed"] is False
    assert view["hybrid_passed"] is None
    assert not ml_blocks_entry(view)
    idea = score_idea(model_base, frames(1), now=NOW)
    assert idea["trades"] is False
    assert idea["direction"] == "RISE"
    assert idea["p_rise"] == 0.71
    assert idea["p_fall"] == 0.29
    assert idea["level"] == "stronger"
    joblib.dump({"pipeline": _FixedProba(0.51), "features": FEATURE_COLUMNS}, folder / "model.joblib")
    from app.forex.ml import predict as predict_module
    predict_module._IDEA_CACHE.clear()
    unsure = score_idea(model_base, frames(1), now=NOW)
    assert unsure["direction"] == "NO TRADE"
    assert unsure["level"] == "low"
    assert unsure["trades"] is False


def test_installed_model_blocks_when_features_unavailable(tmp_path: Path):
    model_base = tmp_path / "models"
    _install_model(model_base, p_rise=0.9)
    thin = {"15m": history(900, count=20), "1h": history(3600, count=10), "4h": history(14400, count=5)}
    view = predict_frames(model_base, thin, now=NOW, checklist_direction="RISE")
    assert view["installed"] is True
    assert view["state"] == "unavailable"
    assert view["hybrid_passed"] is False
    assert ml_blocks_entry(view)
