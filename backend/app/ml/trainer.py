"""
Training orchestrator — multi-model touch probability pipeline.

Trains baseline, XGBoost, CatBoost, LSTM separately; compares weighted and
stacking ensembles on chronological validation; calibrates the selected
pipeline; evaluates once on held-out test; persists artefacts + report.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import structlog
from sklearn.metrics import brier_score_loss, roc_auc_score

from app.config import settings
from app.features.dataset import chronological_split, embargo_overlapping_samples
from app.features.pipeline import apply_feature_prep, fit_feature_prep
from app.ml.artifacts import checksum_paths, dataset_manifest_id, write_run_manifest
from app.ml.dataset_builder import build_touch_dataset
from app.ml.quote_match import match_quotes_to_entries
from app.ml.baseline import (
    HistoricalFrequencyBaseline,
    LogisticRegressionBaseline,
    VolatilityDistanceBaseline,
)
from app.ml.calibration import ProbabilityCalibrator, compute_calibration_metrics
from app.ml.catboost_model import CatBoostTouchModel
from app.ml.ensemble import (
    equal_weights,
    fit_stacking_ensemble,
    fit_weighted_ensemble,
)
from app.ml.evaluation import evaluate_model
from app.ml.lstm_model import LSTMTouchModel
from app.ml.report import write_evaluation_report
from app.ml.validation_gates import GateThresholds, evaluate_promotion_gates
from app.ml.walk_forward_eval import evaluate_outer_folds, fractions_for_lockbox, lockbox_start_index
from app.ml.xgboost_model import XGBoostTouchModel

logger = structlog.get_logger(__name__)

BARRIER_UNIT = "relative_price_points"
CONTRACT_SEMANTICS = (
    "Instrument default: Deriv Volatility 100 Index (API symbol R_100). "
    "Relative barrier inputs such as 0.09 or 0.2 are price-point offsets from "
    "the entry spot (not percent). Target event: probability that the specific "
    "signed barrier is touched by ticks within duration_seconds (default 540) "
    "after contract start, after an assumed manual entry delay. Upper and lower "
    "are separate events and are trained/evaluated separately."
)


def _model_dir() -> str:
    return settings.model_dir


def _safe_auc(y, p) -> float:
    try:
        return float(roc_auc_score(y, p))
    except ValueError:
        return 0.5


def _report_to_dict(report) -> dict:
    d = dict(report.__dict__)
    # Keep JSON-friendly
    return {k: v for k, v in d.items() if k != "calibration_curve_data" or isinstance(v, dict)}


class TrainingOrchestrator:
    """Orchestrates the full multi-model training pipeline."""

    def __init__(
        self,
        barrier_distance: float = 0.09,
        barrier_direction: str = "upper",
        duration_seconds: int = 540,
        include_xgboost: bool = True,
        symbol: str = "R_100",
        entry_delay_seconds: int | None = None,
    ):
        self.barrier_distance = barrier_distance
        self.barrier_direction = barrier_direction
        self.duration_seconds = duration_seconds
        self.include_xgboost = include_xgboost
        self.symbol = symbol
        self.entry_delay_seconds = (
            settings.manual_entry_delay_seconds
            if entry_delay_seconds is None
            else entry_delay_seconds
        )
        self.seq_len = settings.lstm_seq_len
        self.results: dict = {}
        self.models: dict = {}

    def train_full_pipeline(
        self,
        ticks_df: pd.DataFrame,
        sampling_interval_seconds: int | None = None,
        quotes_df: pd.DataFrame | None = None,
        min_calibration_samples: int | None = None,
        min_edge_selected: int | None = None,
    ) -> dict:
        if sampling_interval_seconds is None:
            sampling_interval_seconds = int(settings.train_sampling_interval_seconds)
        # Non-overlapping by default: spacing >= contract duration
        sampling_interval_seconds = max(
            int(sampling_interval_seconds), int(self.duration_seconds)
        )
        self._min_cal = int(
            settings.train_min_calibration_samples
            if min_calibration_samples is None
            else min_calibration_samples
        )
        self._min_edge = int(
            settings.train_edge_min_selected
            if min_edge_selected is None
            else min_edge_selected
        )
        self._min_cal = max(11, min(200, self._min_cal))
        self._min_edge = max(5, min(200, self._min_edge))
        logger.info(
            "training_pipeline_starting",
            n_ticks=len(ticks_df),
            barrier=self.barrier_distance,
            direction=self.barrier_direction,
            symbol=self.symbol,
            barrier_unit=BARRIER_UNIT,
            sampling_interval_seconds=sampling_interval_seconds,
            min_calibration_samples=self._min_cal,
            min_edge_selected=self._min_edge,
        )

        entry_times = self._sample_entry_times(ticks_df, sampling_interval_seconds)
        built = build_touch_dataset(
            ticks_df,
            entry_times,
            barrier_distance=self.barrier_distance,
            barrier_direction=self.barrier_direction,
            duration_seconds=self.duration_seconds,
            entry_delay_seconds=self.entry_delay_seconds,
            seq_len=self.seq_len,
            min_window_ticks=settings.min_label_window_ticks,
        )
        unresolved = built.unresolved
        resolved_count = built.resolved_before_alignment
        logger.info(
            "labels_computed",
            resolved=resolved_count,
            unresolved=unresolved,
            aligned=built.aligned_rows,
        )

        min_labels = 100
        if resolved_count < min_labels:
            span_s = (
                int(ticks_df["epoch"].max()) - int(ticks_df["epoch"].min())
                if not ticks_df.empty
                else 0
            )
            need_span_h = (
                (min_labels * sampling_interval_seconds + 600 + self.duration_seconds) / 3600.0
            )
            return {
                "error": (
                    f"Insufficient resolved labeled data for training: "
                    f"{resolved_count} resolved (need ≥{min_labels}). "
                    f"Non-overlapping samples every {sampling_interval_seconds}s need "
                    f"~{need_span_h:.1f}h continuous tick coverage "
                    f"(current span ≈{span_s/3600.0:.1f}h, {len(ticks_df):,} ticks). "
                    f"Let the worker collect longer, or POST /train/download?target_ticks=0 "
                    f"to pull the last ~24h from Deriv, then train again."
                ),
                "count": resolved_count,
                "min_required": min_labels,
                "span_hours": round(span_s / 3600.0, 2),
                "sampling_interval_seconds": sampling_interval_seconds,
            }

        if built.aligned_rows < 100:
            return {"error": "Could not compute enough aligned feature/sequence rows", "count": built.aligned_rows}

        if built.leakage:
            return {"error": "Future leakage detected in feature construction"}

        features_matrix = built.features
        sequences = built.sequences
        labels_aligned = built.labels
        times_aligned = built.times
        attempted_labels = resolved_count + unresolved

        # Keep sequences paired with tabular rows under chronological sort
        sort_idx = times_aligned.argsort()
        features_matrix = features_matrix.iloc[sort_idx].reset_index(drop=True)
        labels_aligned = labels_aligned.iloc[sort_idx].reset_index(drop=True)
        times_aligned = times_aligned.iloc[sort_idx].reset_index(drop=True)
        sequences = sequences[np.asarray(sort_idx)]

        # Extra embargo if residual overlap remains
        keep = embargo_overlapping_samples(times_aligned, self.duration_seconds)
        if int(keep.sum()) >= 100:
            features_matrix = features_matrix.loc[keep.values].reset_index(drop=True)
            labels_aligned = labels_aligned.loc[keep.values].reset_index(drop=True)
            times_aligned = times_aligned.loc[keep.values].reset_index(drop=True)
            sequences = sequences[np.asarray(keep.values)]

        effective_n = len(times_aligned)
        seq_by_time = {
            pd.Timestamp(t): sequences[i] for i, t in enumerate(times_aligned)
        }

        # Quote match for economic evaluation (fallback breakeven when unmatched)
        entry_epochs = np.array(
            [
                int(t.timestamp()) if hasattr(t, "timestamp") else int(pd.Timestamp(t).timestamp())
                for t in times_aligned
            ],
            dtype=np.int64,
        )
        quote_match = match_quotes_to_entries(
            entry_epochs,
            quotes_df if quotes_df is not None else pd.DataFrame(),
            direction=self.barrier_direction,
            max_age_seconds=int(settings.max_quote_age_seconds),
        )

        gap_seconds = int(settings.train_gap_seconds)
        min_cal = int(getattr(self, "_min_cal", settings.train_min_calibration_samples))
        min_test = int(getattr(self, "_min_edge", settings.train_edge_min_selected))
        wf_summary: dict = {
            "folds_total": 0,
            "folds_eligible": 0,
            "models": {},
            "selected_pipeline": None,
        }
        try:
            wf_summary, _lock_from_folds = evaluate_outer_folds(
                features_matrix,
                labels_aligned,
                times_aligned,
                sequences,
                direction=self.barrier_direction,
                outcome_window_seconds=self.duration_seconds,
                gap_seconds=gap_seconds,
                n_splits=int(settings.wf_outer_folds),
                include_heavy_models=True,
            )
        except Exception as fold_error:
            logger.warning("walk_forward_failed", error=str(fold_error))

        lock_start = lockbox_start_index(
            len(features_matrix),
            fraction=float(settings.lockbox_fraction),
        )
        fracs = fractions_for_lockbox(len(features_matrix), lock_start)
        try:
            split = chronological_split(
                features_df=features_matrix,
                labels=labels_aligned,
                times=times_aligned,
                outcome_window_seconds=self.duration_seconds,
                gap_seconds=gap_seconds,
                train_frac=fracs["train_frac"],
                val_frac=fracs["val_frac"],
                cal_frac=fracs["cal_frac"],
                test_frac=fracs["test_frac"],
                min_cal_samples=min_cal,
                min_test_samples=min(min_test, max(5, len(features_matrix) - lock_start)),
            )
        except ValueError as e:
            return {"error": f"Dataset split failed: {e}"}

        feature_prep = fit_feature_prep(split.X_train)
        feature_cols = list(feature_prep["feature_columns"])
        split.X_train = apply_feature_prep(split.X_train, feature_prep)
        split.X_val = apply_feature_prep(split.X_val, feature_prep)
        split.X_cal = apply_feature_prep(split.X_cal, feature_prep)
        split.X_test = apply_feature_prep(split.X_test, feature_prep)
        wf_folds_eligible = int(wf_summary.get("folds_eligible") or 0)

        if len(split.X_cal) < min_cal:
            # Rough days needed at current non-overlapping spacing
            need_n = max(effective_n + 1, int(min_cal / 0.15) + 40)
            need_h = (need_n * max(self.duration_seconds, 1)) / 3600.0
            return {
                "error": (
                    f"Insufficient calibration samples after purging: {len(split.X_cal)} "
                    f"< {min_cal} (total usable samples={effective_n}). "
                    f"Need roughly ~{need_h:.0f}h more continuous coverage, or lower "
                    f"Min calibration samples in the Train UI (Starter), then retrain."
                )
            }

        def _seqs_for(times: pd.Series) -> np.ndarray:
            mats = []
            for t in times:
                key = pd.Timestamp(t)
                if key in seq_by_time:
                    mats.append(seq_by_time[key])
            if not mats:
                return np.zeros((0, self.seq_len, 4), dtype=np.float32)
            return np.stack(mats, axis=0)

        def _align(X, y, times, seq):
            n = min(len(X), len(y), len(times), len(seq))
            return (
                X.iloc[:n].reset_index(drop=True),
                y.iloc[:n].reset_index(drop=True),
                times.iloc[:n].reset_index(drop=True),
                seq[:n],
            )

        X_train, y_train, t_train, seq_train = _align(
            split.X_train, split.y_train, split.train_times, _seqs_for(split.train_times)
        )
        X_val, y_val, t_val, seq_val = _align(
            split.X_val, split.y_val, split.val_times, _seqs_for(split.val_times)
        )
        X_cal, y_cal, t_cal, seq_cal = _align(
            split.X_cal, split.y_cal, split.cal_times, _seqs_for(split.cal_times)
        )
        X_test, y_test, t_test, seq_test = _align(
            split.X_test, split.y_test, split.test_times, _seqs_for(split.test_times)
        )

        def _label_balance(y) -> tuple[int, int]:
            arr = np.asarray(y, dtype=float).ravel()
            n_pos = int((arr >= 0.5).sum())
            return n_pos, int(len(arr) - n_pos)

        tr_pos, tr_neg = _label_balance(y_train)
        if tr_pos == 0 or tr_neg == 0:
            return {
                "error": (
                    f"Training labels are all the same class "
                    f"(touched={tr_pos}, not_touched={tr_neg}). "
                    f"Barrier {self.barrier_distance} is likely too easy/hard for this span. "
                    f"Collect more days of ticks, or change barrier in Setup/Train and retrain."
                )
            }
        cal_pos, cal_neg = _label_balance(y_cal)
        if cal_pos == 0 or cal_neg == 0:
            logger.warning(
                "calibration_single_class",
                touched=cal_pos,
                not_touched=cal_neg,
                note="Using identity calibrator; prefer more diverse data",
            )

        results: dict = {}
        baseline_rate = float(y_train.mean())
        results["historical_frequency"] = {"touch_rate": baseline_rate}
        results["contract_semantics"] = CONTRACT_SEMANTICS
        results["barrier_unit"] = BARRIER_UNIT
        match_rate = float(quote_match.get("match_rate") or 0.0)
        results["quote_backtest_note"] = (
            f"quote_match_rate={match_rate:.2%}; unmatched rows use fallback "
            "breakeven 0.956 for description only and never for promotion."
        )
        results["quote_match_rate"] = match_rate

        # Early stopping uses a tail of TRAIN. VAL is diagnostic; CAL calibrates;
        # the lockbox (X_test) is scored once after the pipeline is frozen.
        es_n = max(15, int(len(X_train) * 0.15))
        if len(X_train) - es_n >= 30:
            X_fit, y_fit = X_train.iloc[:-es_n], y_train.iloc[:-es_n]
            X_es, y_es = X_train.iloc[-es_n:], y_train.iloc[-es_n:]
            seq_fit = seq_train[:-es_n]
            seq_es = seq_train[-es_n:]
        else:
            X_fit, y_fit, X_es, y_es = X_train, y_train, None, None
            seq_fit, seq_es = seq_train, None

        freq_baseline = HistoricalFrequencyBaseline()
        freq_baseline.fit(pd.DataFrame({"touched": y_train.values, "barrier_direction": self.barrier_direction}))

        lr_model = LogisticRegressionBaseline()
        lr_model.fit(X_train, y_train)
        vol_model = VolatilityDistanceBaseline()
        vol_model.fit(X_train, y_train)

        xgb_model = XGBoostTouchModel(direction=self.barrier_direction)
        xgb_model.fit(X_fit, y_fit, X_es, y_es)

        cb_model = CatBoostTouchModel(direction=self.barrier_direction)
        cb_model.fit(X_fit, y_fit, X_es, y_es)

        lstm_model = LSTMTouchModel(direction=self.barrier_direction)
        try:
            lstm_model.fit(
                seq_fit,
                np.asarray(y_fit),
                seq_es,
                None if y_es is None else np.asarray(y_es),
            )
            lstm_ok = True
        except Exception as e:
            logger.error("lstm_train_failed", error=str(e))
            lstm_ok = False

        # Predictions
        def _preds(X, seq):
            out = {
                "xgboost": xgb_model.predict_proba(X),
                "catboost": cb_model.predict_proba(X),
            }
            if lstm_ok:
                out["lstm"] = lstm_model.predict_proba(seq)
            else:
                out["lstm"] = np.full(len(X), baseline_rate, dtype=float)
            return out

        val_preds = _preds(X_val, seq_val)
        cal_preds = _preds(X_cal, seq_cal)
        test_preds = _preds(X_test, seq_test)

        # Stacking via chronological OOF on TRAIN only (never on base train predictions in-sample)
        from app.ml.ensemble import MODEL_KEYS

        oof = {k: np.full(len(X_train), np.nan) for k in MODEL_KEYS}

        n_tr = len(X_train)
        min_train = max(80, int(n_tr * 0.4))
        n_folds = 5
        fold_size = max(1, (n_tr - min_train) // n_folds)
        for fold in range(n_folds):
            train_end = min_train + fold * fold_size
            test_end = n_tr if fold == n_folds - 1 else min(train_end + fold_size, n_tr)
            if test_end <= train_end:
                continue
            X_tr = X_train.iloc[:train_end]
            y_tr = y_train.iloc[:train_end]
            X_te = X_train.iloc[train_end:test_end]
            seq_tr = seq_train[:train_end]
            seq_te = seq_train[train_end:test_end]

            mx = XGBoostTouchModel(direction=self.barrier_direction, n_estimators=150)
            mx.fit(X_tr, y_tr)
            oof["xgboost"][train_end:test_end] = mx.predict_proba(X_te)

            mc = CatBoostTouchModel(direction=self.barrier_direction, iterations=150)
            mc.fit(X_tr, y_tr)
            oof["catboost"][train_end:test_end] = mc.predict_proba(X_te)

            if lstm_ok:
                try:
                    ml = LSTMTouchModel(direction=self.barrier_direction, max_epochs=8)
                    ml.fit(seq_tr, y_tr.values)
                    oof["lstm"][train_end:test_end] = ml.predict_proba(seq_te)
                except Exception:
                    oof["lstm"][train_end:test_end] = baseline_rate
            else:
                oof["lstm"][train_end:test_end] = baseline_rate

        try:
            stacking = fit_stacking_ensemble(oof, y_train.values)
        except ValueError as e:
            logger.warning("stacking_unavailable", error=str(e))
            stacking = None

        from app.ml.ensemble import MODEL_KEYS as _ENSEMBLE_KEYS

        weight_mask = np.ones(len(X_train), dtype=bool)
        for key in _ENSEMBLE_KEYS:
            weight_mask &= np.isfinite(np.asarray(oof[key], dtype=float))
        if int(weight_mask.sum()) >= 30:
            weighted = fit_weighted_ensemble(
                {key: np.asarray(oof[key])[weight_mask] for key in _ENSEMBLE_KEYS},
                y_train.values[weight_mask],
            )
        else:
            weighted = fit_weighted_ensemble(val_preds, y_val.values)

        # Candidate raw scores on VAL are diagnostic. The frozen choice is the
        # walk-forward winner when one cleared the baseline on enough folds.
        candidates_raw_val = {
            "baseline": np.full(len(y_val), baseline_rate),
            "logistic_regression": lr_model.predict_proba(X_val),
            "volatility_distance": vol_model.predict_proba(X_val),
            "catboost": val_preds["catboost"],
            "lstm": val_preds["lstm"],
            "weighted": weighted.predict(val_preds),
        }
        if self.include_xgboost:
            candidates_raw_val["xgboost"] = val_preds["xgboost"]
        if stacking is not None:
            candidates_raw_val["stacking"] = stacking.predict(val_preds)

        # Select by validation Brier improvement over constant baseline
        baseline_brier = float(
            brier_score_loss(y_val.values, np.clip(candidates_raw_val["baseline"], 1e-6, 1 - 1e-6))
        )
        best_name = "catboost"
        best_brier = float("inf")
        val_scores = {}
        for name, preds in candidates_raw_val.items():
            brier = float(brier_score_loss(y_val.values, np.clip(preds, 1e-6, 1 - 1e-6)))
            val_scores[name] = brier
            # Prefer models that beat baseline; among those, lowest Brier
            if brier < best_brier:
                best_brier = brier
                best_name = name
        if best_brier >= baseline_brier - 1e-9 and "catboost" in candidates_raw_val:
            # No improvement — still pick best, but mark weak selection
            logger.warning(
                "no_val_brier_improvement_over_baseline",
                best=best_name,
                best_brier=best_brier,
                baseline_brier=baseline_brier,
            )
        fold_choice = wf_summary.get("selected_pipeline")
        if fold_choice in candidates_raw_val:
            best_name = str(fold_choice)
            best_brier = float(val_scores.get(fold_choice, best_brier))

        results["validation_selection"] = {
            "metric": "walk_forward_then_val_brier",
            "scores": val_scores,
            "baseline_brier": baseline_brier,
            "selected_pipeline": best_name,
            "brier_improvement": baseline_brier - best_brier,
            "equal_weights_baseline": equal_weights(),
            "chosen_weights": weighted.weights,
            "walk_forward_folds": wf_folds_eligible,
            "walk_forward": wf_summary,
        }
        logger.info("pipeline_selected", selected=best_name, val_brier=best_brier)

        def _selected_raw(preds_map, X=None):
            if best_name == "baseline":
                return np.full(len(next(iter(preds_map.values()))), baseline_rate)
            if best_name == "logistic_regression":
                return lr_model.predict_proba(X if X is not None else X_cal)
            if best_name == "volatility_distance":
                return vol_model.predict_proba(X if X is not None else X_cal)
            if best_name == "weighted":
                return weighted.predict(preds_map)
            if best_name == "stacking":
                if stacking is None:
                    return preds_map["catboost"]
                return stacking.predict(preds_map)
            return preds_map[best_name]

        # Calibrate selected pipeline on CAL only
        sel_cal_raw = _selected_raw(cal_preds, X_cal)
        calibrator = ProbabilityCalibrator(
            method="auto",
            min_samples=int(getattr(self, "_min_cal", settings.train_min_calibration_samples)),
        )
        calibrator.fit(sel_cal_raw, y_cal.values)
        cal_metrics = compute_calibration_metrics(y_cal.values, calibrator.calibrate(sel_cal_raw))

        # Map global quote_match arrays onto test indices
        time_to_i = {pd.Timestamp(t): i for i, t in enumerate(times_aligned)}
        test_idx = [time_to_i[pd.Timestamp(t)] for t in t_test]
        test_be = quote_match["breakeven_probs"][test_idx]
        test_prices = quote_match["quote_prices"][test_idx]
        test_payouts = quote_match["quote_payouts"][test_idx]
        test_matched = quote_match["matched"][test_idx]

        candidate_reports = {}
        builders = [
            ("baseline", lambda p, X: np.full(len(y_test), baseline_rate)),
            ("logistic_regression", lambda p, X: lr_model.predict_proba(X)),
            ("volatility_distance", lambda p, X: vol_model.predict_proba(X)),
            ("catboost", lambda p, X: p["catboost"]),
            ("lstm", lambda p, X: p["lstm"]),
            ("weighted", lambda p, X: weighted.predict(p)),
        ]
        if self.include_xgboost:
            builders.insert(2, ("xgboost", lambda p, X: p["xgboost"]))
        for name, raw_map_builder in builders:
            raw = raw_map_builder(test_preds, X_test)
            proba = calibrator.calibrate(raw) if name == best_name else raw
            rep = evaluate_model(
                name,
                self.barrier_direction,
                y_test.values,
                proba,
                breakeven_probs=test_be,
                quote_prices=np.where(test_matched, test_prices, np.nan),
                quote_payouts=np.where(test_matched, test_payouts, np.nan),
                times=t_test,
                margin_over_breakeven=float(settings.train_edge_margin),
                quote_matched=test_matched,
            )
            candidate_reports[name] = rep
            results[name] = _report_to_dict(rep)

        if stacking is not None:
            raw = stacking.predict(test_preds)
            proba = calibrator.calibrate(raw) if best_name == "stacking" else raw
            rep = evaluate_model(
                "stacking",
                self.barrier_direction,
                y_test.values,
                proba,
                breakeven_probs=test_be,
                quote_prices=np.where(test_matched, test_prices, np.nan),
                quote_payouts=np.where(test_matched, test_payouts, np.nan),
                times=t_test,
                margin_over_breakeven=float(settings.train_edge_margin),
                quote_matched=test_matched,
            )
            candidate_reports["stacking"] = rep
            results["stacking"] = _report_to_dict(rep)

        selected_report = candidate_reports[best_name]
        baseline_report = candidate_reports["baseline"]
        if bool(test_matched.any()):
            mean_be = float(np.mean(test_be[test_matched]))
        else:
            mean_be = float("nan")
        fold_stats = (wf_summary.get("models") or {}).get(best_name) or {}
        fold_win_fraction = float(fold_stats.get("win_fraction") or 0.0)
        lockbox_beats = float(selected_report.brier_score) < float(baseline_report.brier_score)
        unresolved_rate = (
            float(unresolved) / float(attempted_labels) if attempted_labels else 0.0
        )
        has_edge, edge_description = self._compute_edge(
            selected_report,
            mean_breakeven=mean_be,
            quote_match_rate=match_rate,
            wf_fold_count=wf_folds_eligible,
            selected_quote_match_rate=float(getattr(selected_report, "selected_quote_match_rate", 0.0)),
            selected_matched_count=int(getattr(selected_report, "selected_matched_count", 0)),
            calibration_samples=len(X_cal),
            calibration_both_classes=bool(cal_pos and cal_neg),
            unresolved_rate=unresolved_rate,
            fold_win_fraction=fold_win_fraction,
            lockbox_beats_baseline=lockbox_beats,
        )
        results["selected_pipeline"] = best_name
        results["has_demonstrated_edge"] = has_edge
        results["edge_description"] = edge_description
        results["catboost_feature_importance"] = cb_model.feature_importance().to_dict("records")
        results["xgboost_feature_importance"] = xgb_model.feature_importance().to_dict("records")
        results["top_features"] = results["catboost_feature_importance"][:12]
        results["block_bootstrap_ci"] = getattr(selected_report, "block_bootstrap_ci", None)
        results["calibration_curve_data"] = getattr(selected_report, "calibration_curve_data", None)

        # Persist artefacts
        model_dir = _model_dir()
        os.makedirs(model_dir, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        version_tag = f"v-{self.barrier_direction}-{timestamp}"

        paths = {
            "xgboost": os.path.join(model_dir, f"xgboost_{self.barrier_direction}_{timestamp}.pkl"),
            "catboost": os.path.join(model_dir, f"catboost_{self.barrier_direction}_{timestamp}.cbm"),
            "lstm": os.path.join(model_dir, f"lstm_{self.barrier_direction}_{timestamp}.pt"),
            "weighted": os.path.join(model_dir, f"weighted_{self.barrier_direction}_{timestamp}.pkl"),
            "stacking": os.path.join(model_dir, f"stacking_{self.barrier_direction}_{timestamp}.pkl"),
            "calibrator": os.path.join(model_dir, f"calibrator_{self.barrier_direction}_{timestamp}.pkl"),
            "logistic": os.path.join(model_dir, f"logistic_{self.barrier_direction}_{timestamp}.pkl"),
            "volatility": os.path.join(model_dir, f"volatility_{self.barrier_direction}_{timestamp}.pkl"),
        }

        xgb_model.save(paths["xgboost"])
        cb_model.save(paths["catboost"])
        if lstm_ok:
            lstm_model.save(paths["lstm"])
        else:
            paths.pop("lstm", None)
        weighted.save(paths["weighted"])
        if stacking is not None:
            stacking.save(paths["stacking"])
        else:
            paths.pop("stacking", None)
        calibrator.save(paths["calibrator"])
        lr_model.save(paths["logistic"])
        vol_model.save(paths["volatility"])

        path_key = {
            "logistic_regression": "logistic",
            "volatility_distance": "volatility",
            "baseline": "catboost",
        }.get(best_name, best_name)
        primary_path = paths.get(path_key) or paths["catboost"]
        checksums = checksum_paths(paths)
        manifest_id = dataset_manifest_id(
            {
                "symbol": self.symbol,
                "direction": self.barrier_direction,
                "barrier_distance": self.barrier_distance,
                "duration_seconds": self.duration_seconds,
                "entry_delay_seconds": self.entry_delay_seconds,
                "entry_epochs": entry_epochs.tolist(),
            }
        )

        metadata = {
            "version_tag": version_tag,
            "symbol": self.symbol,
            "instrument": self.symbol,
            "direction": self.barrier_direction,
            "barrier_distance": self.barrier_distance,
            "barrier_unit": BARRIER_UNIT,
            "duration_seconds": self.duration_seconds,
            "entry_delay_seconds": self.entry_delay_seconds,
            "feature_columns": feature_cols,
            "feature_prep": feature_prep,
            "strict_features": True,
            "decision_inclusive": True,
            "schema_version": 2,
            "lstm_seq_len": self.seq_len,
            "timestamp": timestamp,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "selected_pipeline": best_name,
            "model_path": primary_path,
            "calibrator_path": paths["calibrator"],
            "paths": paths,
            "ensemble_weights": weighted.weights,
            "baseline_touch_rate": baseline_rate,
            "has_demonstrated_edge": has_edge,
            "edge_description": edge_description,
            "contract_semantics": CONTRACT_SEMANTICS,
            "validation_selection": results["validation_selection"],
            "metrics": {
                "brier_score": selected_report.brier_score,
                "log_loss": selected_report.log_loss_val,
                "auc_roc": selected_report.auc_roc,
                "selected_signal_count": selected_report.selected_signal_count,
                "selected_win_rate": selected_report.selected_win_rate,
                "selected_win_rate_ci_lower": selected_report.selected_win_rate_ci_lower,
                "selected_win_rate_ci_upper": selected_report.selected_win_rate_ci_upper,
                "baseline_win_rate": selected_report.baseline_win_rate,
                "quote_based_net_return": selected_report.quote_based_net_return,
            },
            "split_info": {
                "train": len(X_train),
                "val": len(X_val),
                "cal": len(X_cal),
                "test": len(X_test),
                "purged": split.purged_count,
                "unresolved_labels": unresolved,
                "gap_seconds": gap_seconds,
            },
            "calibration_n_samples": len(X_cal),
            "calibration_method": getattr(calibrator, "method", "isotonic"),
            "sampling_interval_seconds": sampling_interval_seconds,
            "effective_sample_count": effective_n,
            "gap_seconds": gap_seconds,
            "walk_forward_folds": wf_folds_eligible,
            "walk_forward": wf_summary,
            "lockbox_beats_baseline": lockbox_beats,
            "quote_match_rate": match_rate,
            "selected_quote_match_rate": float(getattr(selected_report, "selected_quote_match_rate", 0.0)),
            "mean_quote_breakeven": mean_be,
            "block_bootstrap_ci": results.get("block_bootstrap_ci"),
            "quote_backtest_note": results["quote_backtest_note"],
            "artifact_sha256": checksums,
            "dataset_manifest_id": manifest_id,
            "shadow_started_at": datetime.now(timezone.utc).isoformat(),
            "ece": getattr(selected_report, "ece", None),
            "mce": getattr(selected_report, "mce", None),
        }

        meta_path = os.path.join(model_dir, f"meta_{self.barrier_direction}_{timestamp}.json")
        latest_path = os.path.join(model_dir, f"latest_{self.barrier_direction}.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, default=str)
        with open(latest_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, default=str)
        run_dir = os.path.join(model_dir, "runs", version_tag)
        write_run_manifest(run_dir, metadata)

        report_path = os.path.join(model_dir, f"report_{self.barrier_direction}_{timestamp}.json")
        write_evaluation_report(
            report_path,
            {
                "symbol": self.symbol,
                "direction": self.barrier_direction,
                "barrier_distance": self.barrier_distance,
                "barrier_unit": BARRIER_UNIT,
                "duration_seconds": self.duration_seconds,
                "selected_pipeline": best_name,
                "has_demonstrated_edge": has_edge,
                "edge_description": edge_description,
                "walk_forward": wf_summary,
                "lockbox_beats_baseline": lockbox_beats,
                "contract_semantics": CONTRACT_SEMANTICS,
                "calibration": {
                    **{k: cal_metrics.get(k) for k in ("brier_score", "log_loss", "calibration_slope", "calibration_intercept", "prob_true", "prob_pred")},
                    "n_samples": len(X_cal),
                },
                "candidates": {
                    name: {
                        "val_brier": val_scores.get(name),
                        "test_brier": getattr(rep, "brier_score", None),
                        "test_auc": getattr(rep, "auc_roc", None),
                        "test_selected_signal_count": getattr(rep, "selected_signal_count", None),
                        "test_selected_win_rate_ci_lower": getattr(rep, "selected_win_rate_ci_lower", None),
                        "quote_note": results["quote_backtest_note"],
                    }
                    for name, rep in candidate_reports.items()
                },
                "limitations": (
                    "Final test was evaluated once after selection on validation. "
                    "Alerts remain disabled unless has_demonstrated_edge is true and "
                    "live quote EV filters pass. Zero alerts is acceptable."
                ),
                "version_tag": version_tag,
            },
        )

        results["model_paths"] = {**paths, "metadata": meta_path, "latest": latest_path, "report": report_path}
        results["split_info"] = metadata["split_info"]
        results["timestamp"] = timestamp
        results["version_tag"] = version_tag
        results["metadata"] = metadata
        # Convenience alias for UI expecting catboost block = selected metrics if needed
        results["selected"] = _report_to_dict(selected_report)

        self.results = results
        self.models = {
            "xgboost": xgb_model,
            "catboost": cb_model,
            "lstm": lstm_model if lstm_ok else None,
            "weighted": weighted,
            "stacking": stacking,
            "calibrator": calibrator,
            "logistic": lr_model,
            "frequency": freq_baseline,
        }

        logger.info(
            "training_pipeline_complete",
            selected=best_name,
            has_edge=has_edge,
            version_tag=version_tag,
        )
        return results

    def _compute_edge(
        self,
        report,
        *,
        mean_breakeven: float = 0.956,
        quote_match_rate: float = 0.0,
        wf_fold_count: int = 0,
        selected_quote_match_rate: float | None = None,
        selected_matched_count: int | None = None,
        calibration_samples: int = 0,
        calibration_both_classes: bool = False,
        unresolved_rate: float = 0.0,
        fold_win_fraction: float = 0.0,
        lockbox_beats_baseline: bool = False,
    ) -> tuple[bool, str]:
        decision = evaluate_promotion_gates(
            selected_signal_count=int(report.selected_signal_count),
            selected_win_rate=float(report.selected_win_rate),
            selected_win_rate_ci_lower=float(report.selected_win_rate_ci_lower),
            mean_breakeven=float(mean_breakeven),
            quote_match_rate=float(quote_match_rate),
            selected_quote_match_rate=float(
                quote_match_rate if selected_quote_match_rate is None else selected_quote_match_rate
            ),
            selected_matched_count=int(
                report.selected_signal_count if selected_matched_count is None else selected_matched_count
            ),
            calibration_samples=int(calibration_samples),
            calibration_both_classes=bool(calibration_both_classes),
            unresolved_rate=float(unresolved_rate),
            eligible_folds=int(wf_fold_count),
            fold_win_fraction=float(fold_win_fraction),
            lockbox_beats_baseline=bool(lockbox_beats_baseline),
            thresholds=GateThresholds(
                min_outer_folds=int(settings.promotion_min_outer_folds),
                min_quote_match_rate=float(settings.promotion_min_quote_match),
                min_selected_quote_match_rate=float(settings.promotion_min_selected_quote_match),
                min_selected_signals=max(30, int(getattr(self, "_min_edge", settings.train_edge_min_selected))),
                min_calibration_samples=int(settings.promotion_min_calibration_samples),
                edge_margin=float(settings.train_edge_margin),
            ),
        )
        return decision.passed, decision.summary

    def _sample_entry_times(self, ticks_df: pd.DataFrame, interval_seconds: int) -> pd.Series:
        if ticks_df.empty:
            return pd.Series(dtype="datetime64[ns, UTC]")
        min_epoch = int(ticks_df["epoch"].min())
        max_epoch = int(ticks_df["epoch"].max()) - self.duration_seconds
        if min_epoch >= max_epoch:
            return pd.Series(dtype="datetime64[ns, UTC]")
        entry_epochs = np.arange(min_epoch + 600, max_epoch, interval_seconds)
        return pd.to_datetime(entry_epochs, unit="s", utc=True)
