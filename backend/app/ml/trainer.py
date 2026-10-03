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
from app.features.dataset import chronological_split, embargo_overlapping_samples, walk_forward_splits
from app.features.labels import filter_resolved_labels, label_touch_outcomes
from app.features.pipeline import (
    build_features,
    drop_zero_variance_columns,
    get_feature_columns,
    select_feature_matrix,
    validate_no_future_leakage,
)
from app.ml.quote_match import match_quotes_to_entries
from app.features.sequences import build_price_sequence, stack_sequences
from app.ml.baseline import HistoricalFrequencyBaseline, LogisticRegressionBaseline
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
    ) -> dict:
        if sampling_interval_seconds is None:
            sampling_interval_seconds = int(settings.train_sampling_interval_seconds)
        # Non-overlapping by default: spacing >= contract duration
        sampling_interval_seconds = max(
            int(sampling_interval_seconds), int(self.duration_seconds)
        )
        logger.info(
            "training_pipeline_starting",
            n_ticks=len(ticks_df),
            barrier=self.barrier_distance,
            direction=self.barrier_direction,
            symbol=self.symbol,
            barrier_unit=BARRIER_UNIT,
            sampling_interval_seconds=sampling_interval_seconds,
        )

        entry_times = self._sample_entry_times(ticks_df, sampling_interval_seconds)
        labels_df = label_touch_outcomes(
            ticks_df=ticks_df,
            entry_times=entry_times,
            duration_seconds=self.duration_seconds,
            barrier_distance=self.barrier_distance,
            barrier_direction=self.barrier_direction,
            entry_delay_seconds=self.entry_delay_seconds,
            min_window_ticks=settings.min_label_window_ticks,
        )
        unresolved = int((~labels_df["resolved"]).sum()) if not labels_df.empty else 0
        labels_df = filter_resolved_labels(labels_df)
        logger.info(
            "labels_computed",
            resolved=len(labels_df),
            unresolved=unresolved,
            touched=int(labels_df["touched"].sum()) if not labels_df.empty else 0,
        )

        min_labels = 100
        if labels_df.empty or len(labels_df) < min_labels:
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
                    f"{len(labels_df)} resolved (need ≥{min_labels}). "
                    f"Non-overlapping samples every {sampling_interval_seconds}s need "
                    f"~{need_span_h:.1f}h continuous tick coverage "
                    f"(current span ≈{span_s/3600.0:.1f}h, {len(ticks_df):,} ticks). "
                    f"Let the worker collect longer, or POST /train/download?target_ticks=0 "
                    f"to pull the last ~24h from Deriv, then train again."
                ),
                "count": len(labels_df),
                "min_required": min_labels,
                "span_hours": round(span_s / 3600.0, 2),
                "sampling_interval_seconds": sampling_interval_seconds,
            }

        rows = []
        feature_epochs = []
        entry_epochs = []
        for _, row in labels_df.iterrows():
            entry_time = row["entry_time"]
            entry_epoch = (
                int(entry_time.timestamp())
                if hasattr(entry_time, "timestamp")
                else int(entry_time)
            )
            recent_ticks = ticks_df[ticks_df["epoch"] < entry_epoch].tail(600)
            if len(recent_ticks) < 100:
                continue

            features = build_features(
                recent_ticks,
                barrier_distance=self.barrier_distance,
                barrier_direction=self.barrier_direction,
            )
            if features.empty:
                continue

            seq = build_price_sequence(
                ticks_df,
                entry_epoch=entry_epoch,
                barrier_distance=self.barrier_distance,
                barrier_direction=self.barrier_direction,
                duration_seconds=self.duration_seconds,
                seq_len=self.seq_len,
            )
            if seq is None:
                continue

            feat_row = features.iloc[-1].to_dict()
            feat_epoch = int(feat_row.get("epoch", recent_ticks["epoch"].iloc[-1]))
            feature_epochs.append(feat_epoch)
            entry_epochs.append(entry_epoch)
            rows.append(
                {
                    "features": feat_row,
                    "sequence": seq,
                    "touched": int(bool(row["touched"])),
                    "entry_time": entry_time,
                }
            )

        if len(rows) < 100:
            return {"error": "Could not compute enough aligned feature/sequence rows", "count": len(rows)}

        if not validate_no_future_leakage(feature_epochs, entry_epochs):
            return {"error": "Future leakage detected in feature construction"}

        features_matrix = select_feature_matrix(pd.DataFrame([r["features"] for r in rows]))
        sequences = stack_sequences([r["sequence"] for r in rows])
        labels_aligned = pd.Series([r["touched"] for r in rows], name="touched")
        times_aligned = pd.Series([r["entry_time"] for r in rows], name="entry_time")
        feature_cols = drop_zero_variance_columns(features_matrix, get_feature_columns())
        features_matrix = features_matrix[feature_cols]

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
        min_cal = int(settings.train_min_calibration_samples)
        min_test = int(settings.train_edge_min_selected)
        try:
            split = chronological_split(
                features_df=features_matrix,
                labels=labels_aligned,
                times=times_aligned,
                outcome_window_seconds=self.duration_seconds,
                gap_seconds=gap_seconds,
                feature_columns=feature_cols,
                min_cal_samples=min_cal,
                min_test_samples=min_test,
            )
        except ValueError as e:
            return {"error": f"Dataset split failed: {e}"}

        # Walk-forward fold count for reporting / stability metadata
        wf_folds = walk_forward_splits(
            features_matrix,
            labels_aligned,
            times_aligned,
            n_splits=3,
            outcome_window_seconds=self.duration_seconds,
            gap_seconds=gap_seconds,
            feature_columns=feature_cols,
        )

        if len(split.X_cal) < min_cal:
            # Rough days needed at current non-overlapping spacing
            need_n = max(effective_n + 1, int(min_cal / 0.15) + 40)
            need_h = (need_n * max(self.duration_seconds, 1)) / 3600.0
            return {
                "error": (
                    f"Insufficient calibration samples after purging: {len(split.X_cal)} "
                    f"< {min_cal} (total usable samples={effective_n}). "
                    f"Need roughly ~{need_h:.0f}h more continuous coverage, or lower "
                    f"TRAIN_MIN_CALIBRATION_SAMPLES in .env.prod and recreate backend."
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

        results: dict = {}
        baseline_rate = float(y_train.mean())
        results["historical_frequency"] = {"touch_rate": baseline_rate}
        results["contract_semantics"] = CONTRACT_SEMANTICS
        results["barrier_unit"] = BARRIER_UNIT
        match_rate = float(quote_match.get("match_rate") or 0.0)
        results["quote_backtest_note"] = (
            f"quote_match_rate={match_rate:.2%}; unmatched rows use fallback "
            "breakeven 0.956 and are flagged — not demonstrated profitability "
            "unless match_rate is high and EV edge criteria pass."
        )
        results["quote_match_rate"] = match_rate

        # --- Train base models on TRAIN only (val for early stopping) ---
        freq_baseline = HistoricalFrequencyBaseline()
        freq_baseline.fit(pd.DataFrame({"touched": y_train.values, "barrier_direction": self.barrier_direction}))

        lr_model = LogisticRegressionBaseline()
        lr_model.fit(X_train, y_train)

        xgb_model = XGBoostTouchModel(direction=self.barrier_direction)
        xgb_model.fit(X_train, y_train, X_val, y_val)

        cb_model = CatBoostTouchModel(direction=self.barrier_direction)
        cb_model.fit(X_train, y_train, X_val, y_val)

        lstm_model = LSTMTouchModel(direction=self.barrier_direction)
        try:
            lstm_model.fit(seq_train, y_train.values, seq_val, y_val.values)
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

        # Weighted ensemble (weights from VAL only)
        weighted = fit_weighted_ensemble(val_preds, y_val.values)

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

        # Candidate raw scores on VAL for selection (no test peeking)
        candidates_raw_val = {
            "baseline": np.full(len(y_val), baseline_rate),
            "logistic_regression": lr_model.predict_proba(X_val),
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

        results["validation_selection"] = {
            "metric": "brier_score_vs_baseline",
            "scores": val_scores,
            "baseline_brier": baseline_brier,
            "selected_pipeline": best_name,
            "brier_improvement": baseline_brier - best_brier,
            "equal_weights_baseline": equal_weights(),
            "chosen_weights": weighted.weights,
            "walk_forward_folds": len(wf_folds),
        }
        logger.info("pipeline_selected", selected=best_name, val_brier=best_brier)

        def _selected_raw(preds_map, X=None):
            if best_name == "baseline":
                return np.full(len(next(iter(preds_map.values()))), baseline_rate)
            if best_name == "logistic_regression":
                return lr_model.predict_proba(X if X is not None else X_cal)
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
            min_samples=int(settings.train_min_calibration_samples),
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
            )
            candidate_reports["stacking"] = rep
            results["stacking"] = _report_to_dict(rep)

        selected_report = candidate_reports[best_name]
        mean_be = float(np.nanmean(test_be)) if len(test_be) else 0.956
        has_edge, edge_description = self._compute_edge(
            selected_report,
            mean_breakeven=mean_be,
            quote_match_rate=match_rate,
            wf_fold_count=len(wf_folds),
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

        # Primary model_path for backward-compatible worker loaders
        primary_path = paths.get(best_name) or paths["catboost"]
        if best_name in ("weighted", "stacking", "baseline"):
            primary_path = paths["catboost"]

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
            "walk_forward_folds": len(wf_folds),
            "quote_match_rate": match_rate,
            "mean_quote_breakeven": mean_be,
            "block_bootstrap_ci": results.get("block_bootstrap_ci"),
            "quote_backtest_note": results["quote_backtest_note"],
        }

        meta_path = os.path.join(model_dir, f"meta_{self.barrier_direction}_{timestamp}.json")
        latest_path = os.path.join(model_dir, f"latest_{self.barrier_direction}.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, default=str)
        with open(latest_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, default=str)

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
    ) -> tuple[bool, str]:
        min_selected = int(settings.train_edge_min_selected)
        margin = float(settings.train_edge_margin)
        if report.selected_signal_count < min_selected:
            return False, (
                f"Insufficient selected test signals: "
                f"{report.selected_signal_count} < {min_selected}"
            )
        if quote_match_rate < 0.25:
            return False, (
                f"Insufficient quote coverage for economic edge: "
                f"match_rate={quote_match_rate:.2%} < 25%"
            )
        threshold = float(mean_breakeven) + margin
        if report.selected_win_rate_ci_lower <= threshold:
            return False, (
                f"No demonstrated edge: CI lower "
                f"{report.selected_win_rate_ci_lower:.4f} <= {threshold:.4f} "
                f"(mean quote BE={mean_breakeven:.4f})"
            )
        note = ""
        if wf_fold_count < 2:
            note = " (walk-forward folds < 2 — treat cautiously)"
        return True, (
            f"Edge supported: selected_win_rate={report.selected_win_rate:.4f}, "
            f"CI=[{report.selected_win_rate_ci_lower:.4f}, "
            f"{report.selected_win_rate_ci_upper:.4f}], n={report.selected_signal_count}, "
            f"quote_match={quote_match_rate:.2%}{note}"
        )

    def _sample_entry_times(self, ticks_df: pd.DataFrame, interval_seconds: int) -> pd.Series:
        if ticks_df.empty:
            return pd.Series(dtype="datetime64[ns, UTC]")
        min_epoch = int(ticks_df["epoch"].min())
        max_epoch = int(ticks_df["epoch"].max()) - self.duration_seconds
        if min_epoch >= max_epoch:
            return pd.Series(dtype="datetime64[ns, UTC]")
        entry_epochs = np.arange(min_epoch + 600, max_epoch, interval_seconds)
        return pd.to_datetime(entry_epochs, unit="s", utc=True)
