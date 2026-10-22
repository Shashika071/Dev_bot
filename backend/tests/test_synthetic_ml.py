"""R_100 labels, out-of-time validation and fail-closed model gates."""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.synthetic.analysis import FEATURE_COLUMNS, features_from_frames
from app.synthetic.history import TIMEFRAMES
from app.synthetic.ml import (
    DEFAULT_THRESHOLD,
    DURATION,
    FEATURE_SCHEMA,
    LABEL_KIND,
    POLICY_VERSION,
    _MODEL_CACHE,
    _fit_evaluate,
    _probabilities,
    acceptance,
    artifact_paths,
    build_dataset,
    evaluate,
    label_outcome,
    ml_status,
    predict_frames,
    purged_walk_forward,
    selection_diagnostics,
)

NOW = 1_792_396_800 // 3600 * 3600


def frames() -> dict:
    result = {}
    for tf, step in TIMEFRAMES.items():
        count = 260 * 3600 // step
        rows = []
        for index in range(count):
            open_price = 100 + index * 0.001 + math.sin(index * 0.17) * 0.5
            change = 0 if index % 37 == 0 else 0.04 * math.sin(index * 1.7)
            close = open_price + change
            rows.append({
                "epoch": NOW - (count - index) * step,
                "open": open_price, "high": max(open_price, close) + 0.04,
                "low": min(open_price, close) - 0.04, "close": close,
            })
        result[tf] = rows
    return result


def test_ties_are_losses_for_both_sides():
    assert label_outcome(100, 101, "RISE") == "win"
    assert label_outcome(100, 99, "FALL") == "win"
    assert label_outcome(100, 100, "RISE") == "loss"
    assert label_outcome(100, 100, "FALL") == "loss"
    with pytest.raises(ValueError):
        label_outcome(100, float("nan"), "RISE")


def test_dataset_has_explicit_ties_next_candle_labels_and_live_feature_parity():
    data = frames()
    dataset = build_dataset(data, now=NOW)
    assert len(dataset) > 300
    assert set(dataset["label"]) == {0, 1, 2}
    row = dataset.iloc[-10]
    decision = int(row["decision_epoch"])
    outcome = next(bar for bar in data["1m"] if bar["epoch"] == decision)
    assert row["entry_price"] == outcome["open"]
    assert row["exit_price"] == outcome["close"]
    assert int(row["expiry_epoch"]) == decision + DURATION
    live = features_from_frames(data, now=decision)
    assert live is not None
    np.testing.assert_allclose(row[list(FEATURE_COLUMNS)].to_numpy(dtype=float),
                               np.asarray([live[name] for name in FEATURE_COLUMNS]), rtol=0, atol=0)
    ties = dataset[dataset["entry_price"] == dataset["exit_price"]]
    assert not ties.empty
    assert (ties["label"] == 2).all()


def test_feature_rows_cannot_see_outcome_candle_or_future_higher_timeframes():
    data = frames()
    baseline = build_dataset(data, now=NOW)
    target = baseline.iloc[-30]
    decision = int(target["decision_epoch"])
    altered = copy.deepcopy(data)
    for tf, step in TIMEFRAMES.items():
        for row in altered[tf]:
            if row["epoch"] + step > decision:
                row.update(open=500, close=510, low=490, high=520)
    changed = build_dataset(altered, now=NOW)
    original_features = baseline.loc[baseline["decision_epoch"] <= decision, FEATURE_COLUMNS]
    changed_features = changed.loc[changed["decision_epoch"] <= decision, FEATURE_COLUMNS]
    np.testing.assert_allclose(original_features, changed_features, rtol=0, atol=0)


def test_dataset_removes_missing_outcome_and_rewarms_after_gaps():
    data = frames()
    baseline = build_dataset(data, now=NOW)
    missing = int(baseline.iloc[-30]["decision_epoch"])
    data["1m"] = [bar for bar in data["1m"] if bar["epoch"] != missing]
    changed = build_dataset(data, now=NOW)
    assert missing not in set(changed["decision_epoch"])
    assert (changed["decision_epoch"] < missing).all()


def test_purged_expanding_folds_and_final_test_are_disjoint():
    epochs = np.arange(2000) * 300 + 1_700_000_100
    dataset = pd.DataFrame({"decision_epoch": epochs, "expiry_epoch": epochs + 300})
    folds, development, final = purged_walk_forward(dataset)
    assert len(folds) == 3
    previous_size = 0
    seen_validation = set()
    for train, validation in folds:
        assert len(train) > previous_size
        previous_size = len(train)
        assert train["expiry_epoch"].max() < validation["decision_epoch"].min()
        assert set(train["decision_epoch"]).isdisjoint(set(validation["decision_epoch"]))
        assert seen_validation.isdisjoint(set(validation["decision_epoch"]))
        seen_validation.update(validation["decision_epoch"])
    assert development["expiry_epoch"].max() < final["decision_epoch"].min()
    assert len(final) == 400
    assert set(final["decision_epoch"]).isdisjoint(seen_validation)
    with pytest.raises(ValueError, match="Duplicate"):
        purged_walk_forward(pd.concat([dataset, dataset.iloc[:1]]))


def test_selected_trade_metrics_require_rules_and_count_ties_as_losses():
    dataset = pd.DataFrame({
        "label": [1, 0, 2, 1, 0],
        "checklist_direction": [1, -1, -1, 1, 1],
        "rule_eligible": [1, 1, 1, 0, 1],
    })
    probabilities = np.asarray([
        [0.1, 0.8, 0.1], [0.8, 0.1, 0.1], [0.8, 0.1, 0.1],
        [0.1, 0.8, 0.1], [0.8, 0.1, 0.1],
    ])
    metrics = evaluate(probabilities, dataset)
    assert metrics["selected"] == 3
    assert metrics["selected_wins"] == 2
    assert metrics["selected_ties"] == 1
    assert metrics["selected_accuracy"] == pytest.approx(2 / 3)
    assert metrics["coverage"] == 0.6
    scenario = next(item for item in metrics["payout_sensitivity"] if item["net_payout"] == 0.8)
    assert scenario["expected_return_per_stake"] == pytest.approx((2 / 3) * 0.8 - 1 / 3 - 0.01)


def accepted_report() -> dict:
    candidate = {
        "selected": 200, "selected_accuracy": 0.75, "selected_wilson_lower": 0.68,
        "auc": 0.67, "brier": 0.18,
    }
    control = {"selected": 200, "selected_accuracy": 0.60, "auc": 0.55, "brier": 0.24}
    return {
        "walk_forward": [
            {"fold": index + 1, "catboost": dict(candidate), "logistic": dict(control)} for index in range(3)
        ],
        "final_test": {
            "catboost": candidate, "logistic": control,
            "control_on_candidate_selection_accuracy": 0.60,
        },
    }


def test_promotion_requires_unseen_evidence_control_improvement_and_stability():
    good = accepted_report()
    assert acceptance(good) == (True, [])
    weak = copy.deepcopy(good)
    weak["final_test"]["catboost"]["selected"] = 2
    assert not acceptance(weak)[0]
    weak = copy.deepcopy(good)
    weak["final_test"]["catboost"]["selected_wilson_lower"] = 0.55
    assert not acceptance(weak)[0]
    weak = copy.deepcopy(good)
    weak["final_test"]["catboost"]["brier"] = 0.25
    assert not acceptance(weak)[0]
    weak = copy.deepcopy(good)
    weak["walk_forward"][0]["catboost"]["selected_accuracy"] = 0.5
    assert not acceptance(weak)[0]
    weak = copy.deepcopy(good)
    weak["walk_forward"][0]["catboost"]["selected_accuracy"] = 0.95
    assert not acceptance(weak)[0]
    weak = copy.deepcopy(good)
    weak["final_test"]["control_on_candidate_selection_accuracy"] = 0.80
    assert not acceptance(weak)[0]


class FixedProba:
    classes_ = np.asarray([0, 1, 2])

    def predict_proba(self, values):
        return np.tile([0.1, 0.85, 0.05], (len(values), 1))


def install_valid_test_artifacts(base: Path, data: dict) -> tuple[Path, dict]:
    paths = artifact_paths(base)
    candidate_id = "20261020T000000Z-abcdef12"
    model_path = paths["candidates"] / candidate_id / "catboost.cbm"
    model_path.parent.mkdir(parents=True)
    model_path.write_bytes(b"unit-test-artifact-only")
    report = {
        **accepted_report(), "symbol": "R_100", "duration_seconds": DURATION,
        "entry_timeframe": "1m", "context_timeframes": ["5m", "15m"],
        "feature_schema": FEATURE_SCHEMA, "features": list(FEATURE_COLUMNS),
        "label_kind": LABEL_KIND, "policy_version": POLICY_VERSION,
        "model_kind": "CatBoostClassifier", "promoted": True, "accepted": True,
        "threshold": DEFAULT_THRESHOLD, "candidate_id": candidate_id,
        "model_sha256": hashlib.sha256(model_path.read_bytes()).hexdigest(),
        "feature_origins": {tf: rows[0]["epoch"] for tf, rows in data.items()},
    }
    paths["active"].write_text(json.dumps(report), encoding="utf-8")
    _MODEL_CACHE[str(model_path)] = (model_path.stat().st_mtime_ns, FixedProba())
    return paths["active"], report


def test_missing_or_unpromoted_model_never_passes_hybrid_gate(tmp_path):
    empty = predict_frames(tmp_path, {}, now=NOW, checklist_direction="RISE")
    assert empty["hybrid_passed"] is False
    assert empty["available"] is False
    assert empty["p_rise"] is None
    path = artifact_paths(tmp_path)["latest"]
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"accepted": False, "promoted": False}), encoding="utf-8")
    assert ml_status(tmp_path)["state"] == "invalid"
    blocked = predict_frames(tmp_path, {}, now=NOW, checklist_direction="RISE")
    assert blocked["hybrid_passed"] is False
    assert blocked["p_rise"] is None


def test_unpromoted_candidate_still_shows_rise_and_fall_confidence(tmp_path, monkeypatch):
    data = frames()
    _active, report = install_valid_test_artifacts(tmp_path, data)
    report.update(promoted=False, accepted=False)
    latest = artifact_paths(tmp_path)["latest"]
    latest.write_text(json.dumps(report), encoding="utf-8")
    artifact_paths(tmp_path)["active"].unlink()
    row = {name: 0.0 for name in FEATURE_COLUMNS}
    row.update(checklist_direction=1, rule_eligible=1)
    monkeypatch.setattr("app.synthetic.ml.features_from_frames", lambda *_args, **_kwargs: row)
    result = predict_frames(tmp_path, data, now=NOW, checklist_direction="RISE")
    assert result["promoted"] is False
    assert result["hybrid_passed"] is False
    assert result["p_rise"] == pytest.approx(0.85)
    assert result["p_fall"] == pytest.approx(0.1)
    assert result["direction"] == "RISE"
    assert result["inference_available"] is True
    assert result["display_only"] is True
    assert result["prediction_candidate_id"] == report["candidate_id"]
    assert result["decision_epoch"] == NOW


@pytest.mark.parametrize("metric", ["auc", "brier", "selected_accuracy", "selected_wilson_lower"])
@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), -0.1, 1.1, True, "0.9"])
def test_promotion_rejects_malformed_evidence(metric, bad_value):
    report = accepted_report()
    report["final_test"]["catboost"][metric] = bad_value
    assert acceptance(report)[0] is False


def test_promotion_rejects_malformed_fold_and_paired_control_evidence():
    report = accepted_report()
    report["walk_forward"][0]["catboost"]["selected"] = float("inf")
    assert acceptance(report)[0] is False
    report = accepted_report()
    report["final_test"]["control_on_candidate_selection_accuracy"] = float("nan")
    assert acceptance(report)[0] is False
    report = accepted_report()
    report["walk_forward"] = ["corrupt"] * 3
    assert acceptance(report)[0] is False


def test_selection_diagnostics_explain_each_abstention_stage():
    dataset = pd.DataFrame({"checklist_direction": [1, 1, -1, 1], "rule_eligible": [1, 0, 1, 1]})
    probabilities = np.asarray([[0.49, 0.51, 0], [0.2, 0.8, 0], [0.2, 0.8, 0], [0.2, 0.8, 0]])
    diagnostics = selection_diagnostics(probabilities, dataset)
    assert diagnostics["below_threshold"] == 1
    assert diagnostics["confidence_passed"] == 3
    assert diagnostics["rule_eligible"] == 3
    assert diagnostics["technical_blocked"] == 1
    assert diagnostics["direction_conflicts"] == 1
    assert diagnostics["selected"] == 1
    assert diagnostics["confidence_max"] == pytest.approx(0.8)
    low = selection_diagnostics(np.tile([0.49, 0.51, 0], (4, 1)), dataset)
    assert "No model estimate reached the 60%" in low["reason"]
    assert low["confidence_max"] == pytest.approx(0.51)


def test_incompatible_candidate_explains_why_confidence_needs_retraining(tmp_path):
    data = frames()
    _active, report = install_valid_test_artifacts(tmp_path, data)
    report.update(promoted=False, accepted=False, duration_seconds=300, entry_timeframe="5m")
    artifact_paths(tmp_path)["active"].unlink()
    artifact_paths(tmp_path)["latest"].write_text(json.dumps(report), encoding="utf-8")
    result = predict_frames(tmp_path, data, now=NOW, checklist_direction="RISE")
    assert result["state"] == "invalid"
    assert "retrain" in result["reason"]
    assert result["inference_available"] is False
    assert result["p_rise"] is None


def test_inference_artifact_read_failure_is_contained(tmp_path, monkeypatch):
    def unavailable(*_args):
        raise OSError("candidate temporarily unreadable")

    monkeypatch.setattr("app.synthetic.ml._display_meta", unavailable)
    result = predict_frames(tmp_path, {}, now=NOW, checklist_direction="RISE")
    assert result["state"] == "unavailable"
    assert result["hybrid_passed"] is False
    assert result["p_rise"] is None


def test_missing_candidate_file_explains_unavailable_confidence(tmp_path):
    data = frames()
    _active, report = install_valid_test_artifacts(tmp_path, data)
    report.update(promoted=False, accepted=False)
    artifact_paths(tmp_path)["active"].unlink()
    artifact_paths(tmp_path)["latest"].write_text(json.dumps(report), encoding="utf-8")
    model = artifact_paths(tmp_path)["candidates"] / report["candidate_id"] / "catboost.cbm"
    model.unlink()
    result = predict_frames(tmp_path, data, now=NOW, checklist_direction="RISE")
    assert result["state"] == "invalid"
    assert "model file is missing" in result["reason"]
    assert result["inference_available"] is False
    assert result["hybrid_passed"] is False


def test_inference_reuses_analysis_only_with_matching_history_seed_and_candle(tmp_path, monkeypatch):
    data = frames()
    install_valid_test_artifacts(tmp_path, data)
    row = {name: 0.0 for name in FEATURE_COLUMNS}
    row.update(checklist_direction=1, rule_eligible=1)
    snapshot = {"decision_epoch": NOW, "features": row}
    calls = []

    def calculate(*_args, **_kwargs):
        calls.append(True)
        return row

    monkeypatch.setattr("app.synthetic.ml.features_from_frames", calculate)
    result = predict_frames(tmp_path, data, now=NOW, checklist_direction="RISE", analysis=snapshot)
    assert result["hybrid_passed"] is True
    assert not calls
    snapshot["decision_epoch"] = NOW - 60
    predict_frames(tmp_path, data, now=NOW, checklist_direction="RISE", analysis=snapshot)
    assert len(calls) == 1
    snapshot["decision_epoch"] = NOW
    older_data = copy.deepcopy(data)
    older_data["1m"].insert(0, {**data["1m"][0], "epoch": data["1m"][0]["epoch"] - 60})
    predict_frames(tmp_path, older_data, now=NOW, checklist_direction="RISE", analysis=snapshot)
    assert len(calls) == 2


def test_saved_predictions_add_diagnostics_without_changing_validation_evidence(tmp_path):
    data = frames()
    _active, report = install_valid_test_artifacts(tmp_path, data)
    probability = np.asarray([[0.49, 0.51, 0], [0.48, 0.52, 0]])
    dataset = pd.DataFrame({"label": [1, 0], "checklist_direction": [1, -1], "rule_eligible": [1, 1]})
    metrics = evaluate(probability, dataset)
    metrics.pop("selection_diagnostics")
    report["final_test"] = {"catboost": dict(metrics), "logistic": dict(metrics)}
    artifact_paths(tmp_path)["latest"].write_text(json.dumps(report), encoding="utf-8")
    predictions = dataset.copy()
    for model in ("catboost", "logistic"):
        for index, side in enumerate(("fall", "rise", "tie")):
            predictions[f"{model}_p_{side}"] = probability[:, index]
    path = artifact_paths(tmp_path)["candidates"] / report["candidate_id"] / "final_predictions.csv"
    predictions.to_csv(path, index=False)
    result = ml_status(tmp_path)
    enriched = result["report"]["final_test"]["catboost"]
    assert enriched["selection_diagnostics"]["confidence_max"] == pytest.approx(0.52)
    assert enriched["selected"] == 0
    assert "selection_diagnostics" not in json.loads(artifact_paths(tmp_path)["latest"].read_text())["final_test"]["catboost"]


def test_classifier_probability_rows_and_classes_are_not_silently_broadcast():
    class InvalidRows:
        classes_ = np.asarray([0, 1, 2])

        def predict_proba(self, _values):
            return np.asarray([[0.1, 0.8, 0.1]])

    with pytest.raises(ValueError, match="shape"):
        _probabilities(InvalidRows(), np.ones((3, 2)))
    invalid = InvalidRows()
    invalid.classes_ = np.asarray([0.1, 1, 2])
    with pytest.raises(ValueError, match="classes"):
        _probabilities(invalid, np.ones((1, 2)))


def test_legacy_label_name_is_compatible_only_with_current_one_minute_schema(tmp_path):
    data = frames()
    path, report = install_valid_test_artifacts(tmp_path, data)
    report["label_kind"] = "next_m5_open_close_proxy_ties_lose"
    path.write_text(json.dumps(report), encoding="utf-8")
    assert ml_status(tmp_path)["ready"] is True
    report["duration_seconds"] = 300
    path.write_text(json.dumps(report), encoding="utf-8")
    assert ml_status(tmp_path)["ready"] is False


@pytest.mark.parametrize("key,value", [
    ("symbol", "1HZ100V"), ("duration_seconds", 900),
    ("features", ["wrong_feature"]), ("feature_schema", "old"),
    ("promoted", False), ("feature_origins", {}),
])
def test_incompatible_artifacts_fail_closed(tmp_path, key, value):
    data = frames()
    meta_path, meta = install_valid_test_artifacts(tmp_path, data)
    meta[key] = value
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    result = predict_frames(tmp_path, data, now=NOW, checklist_direction="RISE")
    assert not result["compatible"]
    assert result["hybrid_passed"] is False


def test_checksum_and_training_origins_cannot_silently_drift(tmp_path):
    data = frames()
    meta_path, meta = install_valid_test_artifacts(tmp_path, data)
    shortened = {tf: rows[1:] for tf, rows in data.items()}
    result = predict_frames(tmp_path, shortened, now=NOW, checklist_direction="RISE")
    assert not result["hybrid_passed"]
    assert "origin differs" in result["reason"]
    meta["model_sha256"] = "wrong"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    assert ml_status(tmp_path)["state"] == "invalid"


def test_live_gate_requires_model_rules_and_requested_direction_agreement(tmp_path, monkeypatch):
    data = frames()
    install_valid_test_artifacts(tmp_path, data)
    row = {name: 0.0 for name in FEATURE_COLUMNS}
    row.update(checklist_direction=1, rule_eligible=1)
    monkeypatch.setattr("app.synthetic.ml.features_from_frames", lambda *_args, **_kwargs: row)
    assert predict_frames(tmp_path, data, now=NOW, checklist_direction="RISE")["hybrid_passed"] is True
    assert predict_frames(tmp_path, data, now=NOW, checklist_direction="FALL")["hybrid_passed"] is False
    row["rule_eligible"] = 0
    assert predict_frames(tmp_path, data, now=NOW, checklist_direction="RISE")["hybrid_passed"] is False


def test_real_catboost_and_scaled_control_support_explicit_three_class_probabilities():
    # Exercise real native estimator APIs on tiny deterministic fixture data.
    index = np.arange(180)
    data = pd.DataFrame({name: np.sin(index * 0.1 + number) for number, name in enumerate(FEATURE_COLUMNS)})
    data["decision_epoch"] = 1_700_000_100 + index * 300
    data["expiry_epoch"] = data["decision_epoch"] + 300
    data["label"] = index % 3
    data["checklist_direction"] = np.where(index % 2, 1, -1)
    data["rule_eligible"] = 1
    candidate, control, metrics, predictions = _fit_evaluate(data.iloc[:120], data.iloc[121:],
                                                            iterations=20, threads=1)
    assert metrics["catboost"]["samples"] == 59
    assert len(predictions) == 59
    values = data.iloc[121:][FEATURE_COLUMNS].to_numpy(dtype=float)
    for model in (candidate, control):
        probabilities = _probabilities(model, values)
        assert probabilities.shape == (59, 3)
        np.testing.assert_allclose(probabilities.sum(axis=1), 1)


def test_restart_during_training_is_reported_instead_of_going_idle(tmp_path):
    from app.synthetic.jobs import load_saved_train_job
    from app.synthetic.ml import publish_train_job

    publish_train_job(tmp_path, {
        "status": "running",
        "progress": "Building features 5000 of 120000 closed 1-minute candles",
    })
    job = load_saved_train_job(tmp_path)
    assert job["status"] == "error"
    assert "restarted" in job["error"]
    assert "Idle" not in job["status"]
