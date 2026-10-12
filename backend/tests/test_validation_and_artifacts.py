"""Walk-forward windows, promotion gates, checksums, and frozen promotion."""

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from app.ml.artifacts import sha256_file, verify_artifact_checksums
from app.ml.contract_spec import model_matches_contract
from app.ml.promotion import promotion_blockers
from app.ml.validation_gates import GateThresholds, evaluate_promotion_gates
from app.ml.walk_forward_eval import (
    aggregate_walk_forward,
    lockbox_start_index,
    outer_test_windows,
)


def test_lockbox_is_the_newest_slice_and_folds_stay_inside_development():
    start = lockbox_start_index(400, fraction=0.12)
    assert start < 400
    windows = outer_test_windows(start, n_splits=5)
    assert len(windows) == 5
    assert windows[-1][1] <= start
    for earlier, later in zip(windows, windows[1:]):
        assert earlier[1] <= later[0]


def test_walk_forward_selection_requires_baseline_wins():
    rows = []
    for _ in range(5):
        rows.append({"n_test": 40, "briers": {"baseline": 0.20, "logistic_regression": 0.10}})
    summary = aggregate_walk_forward(rows, min_test=30)
    assert summary["folds_eligible"] == 5
    assert summary["selected_pipeline"] == "logistic_regression"

    losing = []
    for _ in range(5):
        losing.append({"n_test": 40, "briers": {"baseline": 0.10, "logistic_regression": 0.20}})
    assert aggregate_walk_forward(losing, min_test=30)["selected_pipeline"] is None


def test_promotion_gates_fail_closed_without_quotes_or_lockbox():
    failed = evaluate_promotion_gates(
        selected_signal_count=40,
        selected_win_rate=0.99,
        selected_win_rate_ci_lower=0.98,
        mean_breakeven=0.95,
        quote_match_rate=0.1,
        selected_quote_match_rate=0.1,
        selected_matched_count=4,
        calibration_samples=50,
        calibration_both_classes=True,
        unresolved_rate=0.0,
        eligible_folds=5,
        fold_win_fraction=1.0,
        lockbox_beats_baseline=True,
        thresholds=GateThresholds(),
    )
    assert failed.passed is False
    assert "quote coverage" in failed.summary.lower()


def test_checksum_mismatch_fails_and_legacy_metadata_is_allowed(tmp_path):
    path = tmp_path / "model.bin"
    path.write_bytes(b"artifact")
    digest = sha256_file(str(path))
    verify_artifact_checksums({})
    verify_artifact_checksums({"artifact_sha256": {"model": digest}, "paths": {"model": str(path)}})
    with pytest.raises(ValueError):
        verify_artifact_checksums({
            "artifact_sha256": {"model": "0" * 64},
            "paths": {"model": str(path)},
        })


def test_contract_must_match_when_metadata_is_present():
    meta = {
        "symbol": "R_100",
        "direction": "upper",
        "barrier_distance": 0.09,
        "duration_seconds": 540,
        "entry_delay_seconds": 5,
        "barrier_unit": "relative_price_points",
    }
    ok, _ = model_matches_contract(
        meta, symbol="R_100", direction="upper", barrier_distance=0.09,
        duration_seconds=540, entry_delay_seconds=5, barrier_unit="relative_price_points",
    )
    assert ok
    bad, reason = model_matches_contract(
        meta, symbol="R_100", direction="upper", barrier_distance=0.2,
        duration_seconds=540, entry_delay_seconds=5,
    )
    assert bad is False
    assert "barrier" in reason


def test_promotion_does_not_wait_for_edge_or_shadow():
    now = datetime.now(timezone.utc)
    fresh = {
        "has_demonstrated_edge": False,
        "lockbox_beats_baseline": False,
        "shadow_started_at": now.isoformat(),
    }
    assert promotion_blockers(fresh, now=now) == []
    assert promotion_blockers({}, now=now) == []


def test_aggregate_ignores_tiny_folds():
    rows = [{"n_test": 3, "briers": {"baseline": 0.2, "logistic_regression": 0.1}}]
    summary = aggregate_walk_forward(rows, min_test=30)
    assert summary["folds_eligible"] == 0
    assert summary["selected_pipeline"] is None
    assert isinstance(np.mean([1, 2]), float)
    assert isinstance(pd.Timestamp("2026-01-01"), pd.Timestamp)
