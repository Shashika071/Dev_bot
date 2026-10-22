"""Tests for the R_100 research signal engine."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from app.trading_ml.backtesting.contracts import expected_value, settle_contract
from app.trading_ml.config import validate_duration, validate_payout
from app.trading_ml.data.collector import download_ticks
from app.trading_ml.data.quality import assess_candles
from app.trading_ml.data.resample import resample_candles
from app.trading_ml.research.patterns import structure_report
from app.trading_ml.training.search import search_training_params
from app.trading_ml.data.ticks import append_live_ticks, parse_tick_payload, ticks_to_candles
from app.trading_ml.features.causal import FEATURE_COLUMNS, contract_labels, feature_frame
from app.trading_ml.models.regime import filter_hmm_state, fit_volatility_hmm
from app.trading_ml.models.roster import publish_roster
from app.trading_ml.models.sequence import run_sequence_benchmark, sequence_status
from app.trading_ml.monitoring.approval import judge_approval, promotion_allowed
from app.trading_ml.paper_trading.journal import resolve_outcomes, summarize, write_journal
from app.trading_ml.risk.limits import next_stake
from app.trading_ml.service import record_paper, research_root, research_status
from app.trading_ml.signals.engine import apply_live_gates, decide_signal
from app.trading_ml.training.pipeline import run_evaluation
from app.trading_ml.training.splits import embargo_splits


def sample_candles(count: int = 520, seed: int = 7) -> list[dict]:
    rng = np.random.default_rng(seed)
    price = 1000.0
    rows = []
    for index in range(count):
        close = price + math.sin(index / 12) * 0.2 + float(rng.normal(0, 0.35))
        rows.append({
            "epoch": 1_699_999_200 + index * 60,
            "open": price,
            "high": max(price, close) + 0.05,
            "low": min(price, close) - 0.05,
            "close": close,
        })
        price = close
    return rows


def test_quality_reports_gaps_without_filling_them():
    rows = [
        {"epoch": 0, "open": 1, "high": 2, "low": 1, "close": 2},
        {"epoch": 60, "open": 2, "high": 3, "low": 2, "close": 3},
        {"epoch": 60, "open": 2, "high": 3, "low": 2, "close": 3},
        {"epoch": 360, "open": 3, "high": 4, "low": 3, "close": 4},
        {"epoch": 70, "open": 1, "high": 1, "low": 1, "close": 1},
    ]
    report = assess_candles(rows, 60, now=1000)
    assert [row["epoch"] for row in report["candles"]] == [0, 60, 360]
    assert report["gaps"] == 1
    assert report["missing_bars"] == 4
    assert report["duplicates"] == 1
    assert report["invalid"] == 1
    assert report["blocks_signal"] is True


def test_resample_skips_incomplete_hours_and_days():
    candles = sample_candles(60)
    assert resample_candles(candles[:59], 60, 3600) == []
    hour = resample_candles(candles[:60], 60, 3600)
    assert len(hour) == 1
    assert hour[0]["open"] == candles[0]["open"]
    assert hour[0]["close"] == candles[59]["close"]
    assert resample_candles(candles, 60, 86400) == []


def test_ticks_keep_real_prints_and_skip_gapped_minutes():
    payload = {"history": {"times": [0, 1, 1, 3], "prices": [10, 11, 12, 13]}}
    assert len(parse_tick_payload(payload)) == 4
    with pytest.raises(ValueError):
        parse_tick_payload({"history": {"times": [1], "prices": [1, 2]}})
    complete = [{"epoch": second, "quote": 100 + second / 100} for second in range(60)]
    assert len(ticks_to_candles(complete)) == 1
    gapped = [row for row in complete if not 20 <= row["epoch"] <= 26]
    assert ticks_to_candles(gapped) == []


@pytest.mark.asyncio
async def test_tick_download_stops_when_deriv_returns_nothing(tmp_path):
    class FakeClient:
        def __init__(self):
            self.calls = 0

        async def get_ticks_history(self, symbol, **kwargs):
            assert symbol == "R_100"
            self.calls += 1
            if self.calls > 1:
                return {"history": {"times": [], "prices": []}}
            end = int(kwargs["end"])
            return {"history": {"times": [end - 2, end - 1], "prices": [100.0, 101.0]}}

    summary = await download_ticks(tmp_path / "ticks.parquet", hours=1, client=FakeClient(), end=1_700_000_000, pause_seconds=0)
    assert summary["ticks"] == 2
    assert summary["partial"] is True


def test_features_do_not_change_when_a_future_close_changes():
    candles = sample_candles(120)
    before = feature_frame(candles, 60)
    changed = [dict(row) for row in candles]
    changed[-1]["close"] += 25
    changed[-1]["high"] = max(changed[-1]["high"], changed[-1]["close"])
    after = feature_frame(changed, 60)
    pd.testing.assert_series_equal(
        before.iloc[-2][FEATURE_COLUMNS].astype(float),
        after.iloc[-2][FEATURE_COLUMNS].astype(float),
        check_names=False,
    )


def test_contract_label_uses_next_open_and_expiry_close():
    candles = []
    for index in range(30):
        candles.append({
            "epoch": index * 60, "open": 100 + index, "high": 102 + index,
            "low": 99 + index, "close": 100.5 + index,
        })
    labels = contract_labels(candles, duration=120, entry_delay=0, step=60)
    row = labels.loc[labels["epoch"] == 1200].iloc[0]
    assert row["entry_epoch"] == 1260
    assert row["entry_price"] == 121
    assert row["exit_price"] == 122.5
    assert row["label"] == 1
    assert row["expiry_epoch"] == 1380


def test_settlement_is_strict_and_uses_the_supplied_payout():
    assert settle_contract("RISE", 100, 100, 0.95)["outcome"] == "loss"
    assert settle_contract("FALL", 100, 100, 0.95)["outcome"] == "loss"
    rise = settle_contract("RISE", 100, 101, 0.95)
    assert rise["profit_per_stake"] == 0.95
    assert settle_contract("FALL", 100, 99, 0.70)["profit_per_stake"] == 0.70
    assert expected_value(0.60, 0.80) == pytest.approx(0.08)
    assert settle_contract("RISE", 100, 101, None)["profit_per_stake"] is None
    with pytest.raises(ValueError):
        validate_payout(80)
    with pytest.raises(ValueError):
        validate_duration(14400)


def test_embargo_removes_rows_inside_the_horizon():
    frame = pd.DataFrame({
        "decision_epoch": [index * 60 for index in range(160)],
        "expiry_epoch": [index * 60 + 300 for index in range(160)],
        "label": [index % 2 for index in range(160)],
    })
    splits, _, _ = embargo_splits(frame, 300)
    train, validation = splits[0]
    start = int(validation["decision_epoch"].min())
    assert int(train["expiry_epoch"].max()) < start
    assert int(train["decision_epoch"].max()) < start - 300


def test_signal_without_payout_or_approval_is_no_trade():
    card = decide_signal(
        probability_up=0.8, calibrated=True, tie_rate=0.0,
        strategy={"regime": "trending_normal_volatility", "chosen": "trend_following", "direction": "RISE", "reason": "trend"},
        pattern={"opposes_candidate": False, "reason": "inconclusive"},
        volatility={"available": True, "text": "stable"}, payout=None,
        approval_state="SIGNAL_READY", quality_blocks=False, quality_reason=None,
        risk_blockers=[], duration_seconds=300, model_name="logistic", ev_floor=0.03,
    )
    assert card["final_signal"] == "NO TRADE"
    assert card["expected_value"] is None
    assert card["places_trades"] is False


def test_signal_passes_only_when_every_gate_passes():
    card = decide_signal(
        probability_up=0.7, calibrated=True, tie_rate=0.0,
        strategy={"regime": "trending_normal_volatility", "chosen": "trend_following", "direction": "RISE", "reason": "trend"},
        pattern={"opposes_candidate": False, "reason": "neighbors agree"},
        volatility={"available": True, "text": "rv20 forecast 0.010000 versus recent 0.009000 (expansion)"},
        payout=0.95, approval_state="SIGNAL_READY", quality_blocks=False, quality_reason=None,
        risk_blockers=[], duration_seconds=300, model_name="logistic", ev_floor=0.03,
    )
    assert card["final_signal"] == "RISE"
    assert card["calibrated_win_probability"] == pytest.approx(0.7)
    assert card["expected_value"] == pytest.approx(0.365)
    stale = apply_live_gates(card, now=1000, quote_epoch=1000, decision_epoch=900)
    assert stale["final_signal"] == "NO TRADE"
    assert stale["stale"] is True


def test_promotion_ignores_training_accuracy():
    report = {"approval": {"state": "RESEARCH"}, "training_accuracy": 0.99, "test": {"log_loss": 0.01}}
    allowed, reason = promotion_allowed(None, report)
    assert allowed is False
    assert "untouched test" in reason
    ready = judge_approval({
        "calibrated": True, "log_loss": 0.6, "baseline_log_loss": 0.69,
        "brier": 0.2, "baseline_brier": 0.25, "auc": 0.6, "selected": 120,
        "ev_wilson_lower": 0.02,
    }, payout=0.95, min_selected=100)
    assert ready["state"] == "SIGNAL_READY"


def test_roster_lists_every_spec_model_and_only_the_selected_one_votes():
    rows = publish_roster(
        [{
            "name": "logistic", "validation_log_loss": 0.69, "test_log_loss": 0.69,
            "test_auc": 0.5, "test_brier": 0.25, "error": None,
        }],
        ["lightgbm is not installed, so it was not scored and no result was invented."],
        {"reason": "PyTorch is not installed.", "results": None},
        {"reason": "HMM state is research context."},
        {"reason": "Historical neighbors are inconclusive."},
        selected="logistic",
    )
    assert [row["name"] for row in rows] == [
        "logistic", "xgboost", "lightgbm", "catboost", "lstm", "gru", "temporal_cnn", "hmm", "nearest_neighbors",
    ]
    assert rows[0]["votes"] is True
    assert all(row["votes"] is False for row in rows[1:])
    assert rows[2]["test_log_loss"] is None
    assert "not installed" in rows[2]["error"]
    assert rows[4]["test_log_loss"] is None


def test_hmm_filter_ends_in_the_high_volatility_state():
    model = fit_volatility_hmm(np.array([0.01] * 40 + [1.0] * 40))
    assert model is not None
    assert filter_hmm_state(model, np.array([0.01] * 30 + [1.0] * 30)) == 1


def test_sequence_status_does_not_invent_accuracy():
    status = sequence_status()
    assert status["used_for_signal"] is False
    assert status["results"] is None
    benchmark = run_sequence_benchmark(np.linspace(-1, 1, 80), np.array([index % 2 for index in range(80)]))
    assert benchmark["used_for_signal"] is False
    if benchmark["results"] is not None:
        assert set(benchmark["results"]) == {"lstm", "gru", "cnn"}


def test_paper_journal_is_separate_and_stake_stays_fixed(tmp_path):
    rows = resolve_outcomes([
        {"kind": "trade", "source": "paper", "direction": "RISE", "entry_epoch": 60,
         "duration_seconds": 120, "expiry_epoch": 180, "payout": 0.95, "outcome": "open", "stake": 1},
    ], [
        {"epoch": 60, "open": 10, "high": 11, "low": 9, "close": 10.5},
        {"epoch": 120, "open": 10.5, "high": 12, "low": 10, "close": 11},
    ], now=300)
    assert rows[0]["outcome"] == "win"
    assert rows[0]["profit_per_stake"] == 0.95
    assert next_stake(-1) == 1
    path = tmp_path / "paper.jsonl"
    write_journal(path, rows)
    summary = summarize(rows, now=300)
    assert summary["source"] == "paper"
    assert summary["profit"] == 0.95
    assert summary["next_stake"] == 1


def test_parameter_search_fits_only_the_early_training_rows(monkeypatch):
    from app.trading_ml.models.direction import fit_estimator
    seen = []

    def wrapped(name, values, labels, params=None):
        seen.append(len(labels))
        return fit_estimator(name, values, labels, params)

    monkeypatch.setattr("app.trading_ml.training.search.fit_estimator", wrapped)
    values = np.column_stack([np.linspace(-1, 1, 80), np.linspace(1, -1, 80)])
    labels = np.array([0] * 40 + [1] * 40)
    chosen = search_training_params("logistic", values, labels)
    assert seen
    assert max(seen) == 64
    assert set(chosen) <= {"C"}


def test_structure_rates_do_not_authorize_a_trade():
    frame = pd.DataFrame({
        "label": [1, 0, 1, 1],
        "breakout_up": [1, 1, 0, 0],
        "breakout_down": [0, 0, 1, 0],
        "pullback": [0, 0, 0, 1],
    })
    rows = structure_report(frame)
    assert [row["name"] for row in rows] == ["Breakout up", "Breakout down", "Pullback"]
    assert all(row["supports_trade"] is False for row in rows)
    assert rows[0]["samples"] == 2


def test_live_tick_file_appends_new_prints(tmp_path):
    path = tmp_path / "live_ticks.parquet"
    first = append_live_ticks(path, [{"epoch": 10, "quote": 1.2}, {"epoch": 11, "quote": 1.3}])
    second = append_live_ticks(path, [{"epoch": 11, "quote": 1.3}, {"epoch": 12, "quote": 1.4}])
    assert first["ticks"] == 2
    assert second["ticks"] == 3
    assert second["duplicates"] == 1


def test_research_evaluation_stays_fail_closed_on_noise(tmp_path):
    report, _bundle = run_evaluation(
        sample_candles(), duration_seconds=300, payout=0.95, models=["logistic"], now=1_800_000_000,
    )
    assert report["approval_state"] != "SIGNAL_READY"
    assert report["signal"]["final_signal"] == "NO TRADE"
    assert report["places_trades"] is False
    assert report["backtest"]["source"] == "backtest"
    assert report["backtest"]["payout"] == 0.95
    assert report["optional_research"]["sequence"]["used_for_signal"] is False
    assert report["optional_research"]["hyperparameters"]["logistic"] is not None
    assert report["optional_research"]["structures"]
    assert all(row["supports_trade"] is False for row in report["optional_research"]["structures"])
    from app.trading_ml.service import _persist
    _persist(tmp_path, report, _bundle)
    status = research_status(tmp_path, now=1_800_000_000, quote_epoch=1_800_000_000)
    assert status["final_signal"] == "NO TRADE"
    assert research_root(tmp_path) == tmp_path / "synthetic_r100" / "trading_ml"
    recorded = record_paper(tmp_path, now=1_800_000_000, quote_epoch=1_800_000_000)
    assert recorded["paper"]["rejections"] == 1
    assert recorded["paper"]["recorded"] == 0
    assert recorded["backtest"]["source"] == "backtest"
