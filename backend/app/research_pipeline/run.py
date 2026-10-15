"""Chronological training, frozen selection, and one final test evaluation."""

from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from app.research_pipeline.dataset import assemble_frame
from app.research_pipeline.evaluate import (
    approximate_independent_count,
    binary_logloss,
    breakeven_probability,
    by_period,
    choose_feature_group,
    equal_weight_beats_best,
    metrics_block,
    safe_auc,
    select_threshold,
    write_calibration_svg,
    write_markdown,
)
from app.research_pipeline.features import (
    columns_for,
    future_mutation_changes_features,
    lookup_features,
    sequence_matrix,
)
from app.research_pipeline.infer import append_jsonl, score_asof
from app.research_pipeline.io_ticks import matched_net_profit_r
from app.research_pipeline.modeling import (
    apply_calibrator,
    choose_depth,
    design_matrix,
    fit_calibrator,
    fit_constant,
    fit_logistic,
    fit_momentum,
    fit_tree,
    inner_masks,
    predict_bundle,
    train_lstm,
)
from app.research_pipeline.quality import prepare_ticks
from app.research_pipeline.settings import ExperimentConfig
from app.research_pipeline.splits import dependence_block_length, final_split, walk_forward_masks

MODEL_NAMES = ["constant", "momentum", "logistic", "xgboost", "catboost"]
TARGET_COLUMN = {
    "direction": "y_direction",
    "upper_touch": "y_upper",
    "lower_touch": "y_lower",
}
UNRESOLVED_SETTINGS = [
    "Exact instrument, including whether it is standard V100 (R_100) or V100 (1s).",
    "Prediction target.",
    "Expiry or prediction horizon.",
    "Barrier distance and units, if the target is a touch.",
    "Historical contract quotes for that barrier and expiry.",
]


def run_experiment(
    raw: pd.DataFrame,
    cfg: ExperimentConfig,
    out_dir: str | Path,
    quotes: pd.DataFrame | None = None,
) -> dict:
    destination = Path(out_dir)
    destination.mkdir(parents=True, exist_ok=True)
    clean, quality = prepare_ticks(raw, cfg.symbol if cfg.symbol else None)
    report = {
        "banner": cfg.banner(),
        "experimental": not cfg.confirmed_trading_settings,
        "confirmed_trading_settings": bool(cfg.confirmed_trading_settings),
        "unresolved_settings": [] if cfg.confirmed_trading_settings else list(UNRESOLVED_SETTINGS),
        "settings": cfg.to_public_dict(),
        "data_quality": quality,
        "targets": {},
        "orders_placed": False,
    }
    if clean is None:
        report["status"] = quality.get("status", "unusable")
        _write_report(destination, report)
        return report

    assembled = assemble_frame(clean, cfg)
    tables = assembled["tables"]
    quality = dict(quality)
    quality["completed_1m_candles"] = tables["completed_1m"]
    quality["completed_15s_candles"] = tables["completed_15s"]
    quality["gapped_1m_candles_excluded"] = tables["gapped_1m"]
    quality["gapped_15s_candles_excluded"] = tables["gapped_15s"]
    quality["decision_timestamps"] = assembled["decision_count"]
    quality["feature_rows"] = assembled["feature_row_count"]
    quality["label_status_counts"] = assembled["label_counts"]
    quality["direction_ties"] = assembled["tie_count"]
    quality["tick_count_is_not_volume"] = True
    leak_asof = _leak_asof(tables, cfg)
    if leak_asof is None:
        quality["future_tick_changes_earlier_features"] = None
        quality["leakage_check"] = "skipped"
        report["data_quality"] = quality
        report["status"] = "leakage_check_unavailable"
        _write_report(destination, report)
        return report
    leaked = future_mutation_changes_features(tables["epochs"], tables["quotes"], leak_asof, cfg)
    quality["leakage_check_asof"] = int(leak_asof)
    quality["future_tick_changes_earlier_features"] = bool(leaked)
    report["data_quality"] = quality
    (destination / "data_quality.json").write_text(json.dumps(_jsonable(quality), indent=2), encoding="utf-8")
    if leaked:
        report["status"] = "leakage_check_failed"
        _write_report(destination, report)
        return report

    frame = assembled["frame"]
    for target in cfg.resolved_targets():
        report["targets"][target] = _train_target(
            frame,
            tables,
            clean,
            cfg,
            target,
            destination / target,
            quotes,
        )
    report["status"] = "completed"
    _write_report(destination, report)
    return report


def _train_target(frame, tables, clean, cfg: ExperimentConfig, target: str, out_dir: Path, quotes) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    y_col = TARGET_COLUMN[target]
    if frame.empty or y_col not in frame.columns:
        return _stopped(target, "no labeled rows were built")
    work = frame.dropna(subset=[y_col]).sort_values("entry_epoch").reset_index(drop=True)
    required = columns_for("strategy", target)
    finite = np.isfinite(work.loc[:, required].to_numpy(dtype=float)).all(axis=1)
    work = work.loc[finite].reset_index(drop=True)
    labeled = int(len(work))
    block = dependence_block_length(cfg.horizon_seconds, cfg.sample_every_seconds)
    summary = {
        "target": target,
        "labeled_examples": labeled,
        "approximate_independent_outcomes": approximate_independent_count(
            work["entry_epoch"].to_numpy() if labeled else [],
            cfg.horizon_seconds,
        ),
        "dependence_block_length": block,
        "test_opened": False,
        "limitation": None,
    }
    if labeled < int(cfg.min_labeled_rows):
        summary["limitation"] = (
            f"{labeled} labeled examples are below the minimum of {cfg.min_labeled_rows}. "
            "The final test was not opened."
        )
        return summary

    dev_mask, test_mask, purged_mask, test_start = final_split(
        work["entry_epoch"].to_numpy(),
        cfg.horizon_seconds,
        cfg.test_fraction,
    )
    dev = work.loc[dev_mask].reset_index(drop=True)
    test = work.loc[test_mask].reset_index(drop=True)
    summary["rows"] = {
        "development": int(len(dev)),
        "purged": int(purged_mask.sum()),
        "test": int(len(test)),
        "test_start_epoch": test_start,
    }
    if len(dev) < 40 or len(test) < 8:
        summary["limitation"] = "The chronological split does not leave enough development and test rows."
        return summary

    n_blocks = 4 if len(dev) >= 200 else 3
    folds = walk_forward_masks(dev["entry_epoch"].to_numpy(), cfg.horizon_seconds, n_blocks=n_blocks)
    if not folds:
        summary["limitation"] = "Walk-forward folds could not be built."
        return summary

    group_info = _ablate(dev, folds, target, y_col, cfg)
    group = group_info["selected_group"]
    columns = columns_for(group, target)
    oof, depths, errors = _walk_forward(dev, folds, columns, y_col, cfg)
    summary["errors"] = errors
    if oof.empty:
        summary["limitation"] = "No walk-forward predictions were produced."
        summary["feature_ablation"] = group_info
        return summary

    shuffle = _shuffled_label_check(dev, columns, y_col, cfg)
    payout = _payout(quotes, dev["entry_epoch"], target, cfg)
    frozen, late = _freeze_selection(oof, dev, tables, folds, columns, y_col, cfg, group, payout, block)
    frozen["feature_ablation"] = group_info
    frozen["shuffled_label_check"] = shuffle
    frozen["payout"] = payout
    frozen["tree_depths"] = depths
    frozen["validation_rows"] = int(len(oof))
    frozen["baseline_logloss"] = _model_logloss(late, "constant") if "constant" in late.columns else None

    bundle = _refit(dev, tables, columns, y_col, cfg, frozen, target)
    version = f"research-pipeline-1-{target}-{frozen['model']}-seed{cfg.seed}"
    bundle["version"] = version
    joblib.dump(bundle, out_dir / "model.joblib")
    (out_dir / "feature_schema.json").write_text(
        json.dumps(
            {
                "target": target,
                "feature_group": group,
                "columns": columns,
                "candle_rule": "A candle is used only after its bucket has closed.",
                "tick_count_is_not_a_feature": True,
                "barrier_unit": cfg.barrier_unit,
                "horizon_seconds": cfg.horizon_seconds,
                "experimental": not cfg.confirmed_trading_settings,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (out_dir / "preprocess.json").write_text(
        json.dumps(_jsonable({"fills": bundle["fills"], "trees_unscaled": True, "logistic_scaler": "inside logistic bundle"}), indent=2),
        encoding="utf-8",
    )
    (out_dir / "calibration.json").write_text(
        json.dumps(_jsonable({name: {"type": cal.get("type")} for name, cal in bundle["calibrators"].items()}), indent=2),
        encoding="utf-8",
    )
    (out_dir / "threshold.json").write_text(json.dumps(_jsonable(frozen["threshold"]), indent=2), encoding="utf-8")

    opened = {"done": False}

    def _once(y, p, epochs):
        if opened["done"]:
            raise RuntimeError("final test was opened more than once")
        opened["done"] = True
        return metrics_block(
            y,
            p,
            block=block,
            n_boot=cfg.n_bootstrap,
            seed=cfg.seed,
            threshold=frozen["threshold"]["threshold"],
            net_profit_r=payout["r"],
        )

    p_test, y_test = _predict_frame(bundle, test, tables, columns)
    p_base, _ = _predict_frame(_baseline_only(bundle, dev, y_col), test, tables, columns)
    test_metrics = _once(y_test, p_test, test["entry_epoch"].to_numpy())
    test_metrics["constant_baseline_logloss"] = binary_logloss(y_test, p_base)
    test_metrics["by_period"] = by_period(test["entry_epoch"].to_numpy(), y_test, p_test)
    test_metrics["approximate_independent_outcomes"] = approximate_independent_count(
        test["entry_epoch"].to_numpy(),
        cfg.horizon_seconds,
    )
    if frozen["threshold"]["threshold"] is not None:
        signaled = p_test >= frozen["threshold"]["threshold"]
        test_metrics["signal_count"] = int(signaled.sum())
        test_metrics["signal_approximate_independent_outcomes"] = approximate_independent_count(
            test.loc[signaled, "entry_epoch"].to_numpy() if signaled.any() else [],
            cfg.horizon_seconds,
        )
    write_calibration_svg(out_dir / "calibration.svg", test_metrics["calibration_bins"])
    summary.update(
        {
            "test_opened": True,
            "selection": _public_selection(frozen),
            "test_metrics": test_metrics,
            "model_version": version,
            "financial": _financial_note(payout, test_metrics),
        }
    )
    try:
        demo = score_asof(clean, bundle, int(clean["epoch"].iloc[-1]), cfg=cfg)
    except Exception as exc:
        demo = {
            "timestamp": int(clean["epoch"].iloc[-1]),
            "instrument": cfg.symbol,
            "target": target,
            "decision": "no_signal",
            "reason": f"demo inference failed: {exc}",
            "order_placed": False,
            "data_quality": "incomplete",
            "calibrated_probability": None,
            "model_version": version,
        }
    append_jsonl(out_dir / "demo_signals.jsonl", demo)
    summary["demo_signal"] = demo
    (out_dir / "evaluation.json").write_text(json.dumps(_jsonable(summary), indent=2), encoding="utf-8")
    return summary


def _ablate(dev, folds, target: str, y_col: str, cfg: ExperimentConfig) -> dict:
    losses = {name: [] for name in ("returns_vol", "indicators", "strategy")}
    used = 0
    for train_mask, val_mask in folds:
        train = dev.loc[train_mask].reset_index(drop=True)
        val = dev.loc[val_mask].reset_index(drop=True)
        if len(train) < 20 or len(val) < 8 or train[y_col].nunique() < 2:
            continue
        fold_loss = {}
        for group in losses:
            columns = columns_for(group, target)
            predicted = _fold_predict(train, val, "logistic", columns, None, y_col, cfg)
            if predicted is None:
                fold_loss = None
                break
            fold_loss[group] = binary_logloss(val[y_col].to_numpy(), predicted)
        if fold_loss is None:
            continue
        used += 1
        for group, loss in fold_loss.items():
            losses[group].append(loss)
    chosen = choose_feature_group(losses)
    chosen["folds_used"] = used
    chosen["fold_logloss"] = losses
    return chosen


def _walk_forward(dev, folds, columns, y_col, cfg: ExperimentConfig):
    errors = []
    depths = {"xgboost": 2, "catboost": 3}
    first_train = dev.loc[folds[0][0]].reset_index(drop=True)
    if len(first_train) >= 20 and first_train[y_col].nunique() > 1:
        x, _ = design_matrix(first_train, columns)
        y = first_train[y_col].to_numpy()
        epochs = first_train["entry_epoch"].to_numpy()
        for kind in ("xgboost", "catboost"):
            try:
                depths[kind] = choose_depth(kind, x, y, epochs, cfg.horizon_seconds, cfg)
            except Exception as exc:
                errors.append(f"{kind} depth search skipped: {exc}")
    fold_rows = []
    for train_mask, val_mask in folds:
        train = dev.loc[train_mask].reset_index(drop=True)
        val = dev.loc[val_mask].reset_index(drop=True)
        if len(train) < 20 or len(val) < 8 or train[y_col].nunique() < 2:
            continue
        preds = {}
        for name in MODEL_NAMES:
            try:
                preds[name] = _fold_predict(train, val, name, columns, depths.get(name), y_col, cfg)
            except Exception as exc:
                preds[name] = None
                errors.append(f"{name} fold failed: {exc}")
        fold_rows.append((val, preds))
    if not fold_rows:
        return pd.DataFrame(), depths, errors
    common = [name for name in MODEL_NAMES if all(preds.get(name) is not None for _, preds in fold_rows)]
    pieces = []
    for val, preds in fold_rows:
        piece = pd.DataFrame({"entry_epoch": val["entry_epoch"].to_numpy(), "y": val[y_col].to_numpy(dtype=float)})
        for name in common:
            piece[name] = preds[name]
        pieces.append(piece)
    oof = pd.concat(pieces, ignore_index=True).sort_values("entry_epoch").reset_index(drop=True)
    return oof, depths, errors


def _fold_predict(train, val, kind, columns, depth, y_col, cfg: ExperimentConfig):
    y = train[y_col].to_numpy(dtype=float)
    if len(np.unique(y)) < 2:
        return None
    x, fills = design_matrix(train, columns)
    x_val, _ = design_matrix(val, columns, fills)
    if kind == "constant":
        return predict_bundle(fit_constant(y), x_val)
    if kind == "momentum":
        bundle = fit_momentum(train["ret_1m"].to_numpy(dtype=float), y)
        return predict_bundle(bundle, x_val, ret=val["ret_1m"].to_numpy(dtype=float))
    if kind == "logistic":
        return predict_bundle(fit_logistic(x, y, cfg.seed), x_val)
    epochs = train["entry_epoch"].to_numpy()
    fit_mask, es_mask = inner_masks(epochs, cfg.horizon_seconds)
    y_fit = y[fit_mask]
    if int(fit_mask.sum()) >= 15 and int(es_mask.sum()) >= 8 and len(np.unique(y_fit)) == 2:
        bundle = fit_tree(kind, x[fit_mask], y_fit, x[es_mask], y[es_mask], cfg, int(depth or 2))
    else:
        bundle = fit_tree(kind, x, y, None, None, cfg, int(depth or 2))
    return predict_bundle(bundle, x_val)


def _freeze_selection(oof, dev, tables, folds, columns, y_col, cfg, group, payout, block) -> tuple[dict, pd.DataFrame]:
    early, late, shared = _time_halves(oof)
    model_names = [name for name in MODEL_NAMES if name in oof.columns]
    calibrators = {}
    late_cal = {"entry_epoch": late["entry_epoch"].to_numpy(), "y": late["y"].to_numpy(dtype=float)}
    for name in model_names:
        calibrators[name] = fit_calibrator(early[name].to_numpy(), early["y"].to_numpy())
        late_cal[name] = apply_calibrator(calibrators[name], late[name].to_numpy())
    late_frame = pd.DataFrame(late_cal)
    calibrated = {name: late_frame[name].to_numpy() for name in model_names}
    comparison = equal_weight_beats_best(late_frame["y"].to_numpy(), calibrated)
    stacker = None
    if comparison["kept"] and len(early) >= 30 and len(late) >= 20:
        stacker, stack_loss = _try_stack(early, late, model_names, calibrators)
        if stacker is not None and stack_loss + 1e-6 < comparison["ensemble_logloss"]:
            comparison["stacker_kept"] = True
            comparison["stacker_logloss"] = stack_loss
        else:
            stacker = None
            comparison["stacker_kept"] = False
    chosen_name = "ensemble" if comparison["kept"] else comparison["best_single"]
    if comparison["kept"]:
        chosen_p = np.mean(np.column_stack([calibrated[name] for name in model_names]), axis=1)
        if stacker is not None:
            chosen_name = "stack"
            chosen_p = stacker.predict_proba(np.column_stack([calibrated[name] for name in model_names]))[:, 1]
    else:
        chosen_p = calibrated[comparison["best_single"]]
    threshold = select_threshold(
        late_frame["y"].to_numpy(),
        chosen_p,
        payout["r"],
        block,
        cfg.n_bootstrap,
        cfg.seed,
    )
    lstm = _maybe_lstm(dev, tables, folds, y_col, cfg, oof, comparison["best_single"])
    if lstm.get("selected"):
        chosen_name = "lstm"
        comparison = {"kept": False, "reason": "LSTM beat the best tree on the last validation fold", "best_single": "lstm"}
        threshold = select_threshold(
            lstm["y"],
            lstm["p"],
            payout["r"],
            block,
            cfg.n_bootstrap,
            cfg.seed,
        )
        calibrators["lstm"] = lstm["calibrator"]
    frozen = {
        "feature_group": group,
        "model": chosen_name,
        "ensemble": comparison,
        "threshold": threshold,
        "calibrators": calibrators,
        "model_names": model_names,
        "stacker": stacker if chosen_name == "stack" else None,
        "lstm": {key: value for key, value in lstm.items() if key not in ("y", "p", "bundle", "calibrator")},
        "lstm_bundle": lstm.get("bundle"),
        "shared_validation_limitation": shared,
        "validation_logloss": binary_logloss(late_frame["y"].to_numpy(), chosen_p) if chosen_name != "lstm" else lstm.get("val_logloss"),
        "validation_auc": safe_auc(late_frame["y"].to_numpy(), chosen_p) if chosen_name != "lstm" else lstm.get("val_auc"),
    }
    return frozen, late_frame


def _maybe_lstm(dev, tables, folds, y_col, cfg: ExperimentConfig, oof, best_single: str) -> dict:
    if not cfg.enable_lstm:
        return {"status": "disabled", "selected": False}
    train_mask, val_mask = folds[-1]
    train_idx = np.flatnonzero(train_mask)
    val_idx = np.flatnonzero(val_mask)
    sequences, ok = sequence_matrix(tables, dev["entry_epoch"].to_numpy(), cfg.lstm_seq_len)
    y = dev[y_col].to_numpy(dtype=float)
    fit_mask, es_mask = inner_masks(dev["entry_epoch"].to_numpy()[train_idx], cfg.horizon_seconds)
    fit_idx = train_idx[fit_mask & ok[train_idx]]
    es_idx = train_idx[es_mask & ok[train_idx]]
    val_ok = val_idx[ok[val_idx]]
    bundle, info = train_lstm(sequences[fit_idx], y[fit_idx], sequences[es_idx], y[es_idx], cfg)
    if bundle is None:
        info["selected"] = False
        return info
    from app.research_pipeline.modeling import _predict_lstm

    comparison_p = _predict_lstm(bundle, sequences[val_ok])
    comparison_y = y[val_ok]
    loss = binary_logloss(comparison_y, comparison_p)
    epochs = dev["entry_epoch"].to_numpy()[val_ok]
    tree_loss = _aligned_logloss(oof, epochs, best_single)
    selected = tree_loss is not None and loss + 1e-4 < tree_loss
    calibrator = fit_calibrator(_predict_lstm(bundle, sequences[es_idx]), y[es_idx])
    calibrated = apply_calibrator(calibrator, comparison_p)
    return {
        "status": "trained",
        "selected": bool(selected),
        "val_logloss": loss,
        "tree_logloss_same_timestamps": tree_loss,
        "val_auc": safe_auc(comparison_y, calibrated),
        "y": comparison_y,
        "p": calibrated,
        "bundle": bundle,
        "calibrator": calibrator,
        "reason": "kept" if selected else "no reliable improvement over the best tree on the same timestamps",
    }


def _refit(dev, tables, columns, y_col, cfg, frozen, target) -> dict:
    y = dev[y_col].to_numpy(dtype=float)
    x, fills = design_matrix(dev, columns)
    epochs = dev["entry_epoch"].to_numpy()
    fit_mask, es_mask = inner_masks(epochs, cfg.horizon_seconds)
    models = {}
    names = frozen["model_names"]
    if frozen["model"] in ("ensemble", "stack"):
        names = list(frozen["model_names"])
    elif frozen["model"] == "lstm":
        names = ["lstm"]
    else:
        names = [frozen["model"]]
    for name in names:
        if name == "constant":
            models[name] = fit_constant(y)
        elif name == "momentum":
            models[name] = fit_momentum(dev["ret_1m"].to_numpy(dtype=float), y)
        elif name == "logistic":
            models[name] = fit_logistic(x, y, cfg.seed)
        elif name in ("xgboost", "catboost"):
            depth = int(frozen["tree_depths"].get(name, 2))
            if int(fit_mask.sum()) >= 15 and int(es_mask.sum()) >= 8 and len(np.unique(y[fit_mask])) == 2:
                models[name] = fit_tree(name, x[fit_mask], y[fit_mask], x[es_mask], y[es_mask], cfg, depth)
            else:
                models[name] = fit_tree(name, x, y, None, None, cfg, depth)
        elif name == "lstm":
            models[name] = _refit_lstm(dev, tables, y_col, cfg)
    primary = frozen["model"] if frozen["model"] not in ("ensemble", "stack") else frozen["ensemble"].get("best_single")
    if frozen["model"] == "lstm":
        primary = "lstm"
    return {
        "target": target,
        "columns": columns,
        "fills": fills,
        "models": models,
        "calibrators": frozen["calibrators"],
        "primary_model": primary,
        "ensemble_members": names if frozen["model"] in ("ensemble", "stack") else None,
        "stacker": frozen["stacker"],
        "threshold": frozen["threshold"]["threshold"],
        "config": cfg.to_public_dict(),
        "feature_group": frozen["feature_group"],
        "experimental": not cfg.confirmed_trading_settings,
    }


def _refit_lstm(dev, tables, y_col, cfg: ExperimentConfig):
    sequences, ok = sequence_matrix(tables, dev["entry_epoch"].to_numpy(), cfg.lstm_seq_len)
    y = dev[y_col].to_numpy(dtype=float)
    idx = np.flatnonzero(ok)
    fit_mask, es_mask = inner_masks(dev["entry_epoch"].to_numpy()[idx], cfg.horizon_seconds)
    bundle, info = train_lstm(sequences[idx][fit_mask], y[idx][fit_mask], sequences[idx][es_mask], y[idx][es_mask], cfg)
    if bundle is None:
        raise RuntimeError(info.get("reason", "LSTM refit failed"))
    return bundle


def _predict_frame(bundle, frame, tables, columns):
    y_col = TARGET_COLUMN[bundle["target"]]
    y = frame[y_col].to_numpy(dtype=float)
    x, _ = design_matrix(frame, columns, bundle["fills"])
    members = bundle["ensemble_members"] or [bundle["primary_model"]]
    columns_p = []
    sequences = None
    ok = None
    if "lstm" in members:
        sequences, ok = sequence_matrix(tables, frame["entry_epoch"].to_numpy(), bundle["models"]["lstm"]["seq_len"])
        if not ok.all():
            raise RuntimeError("LSTM test rows are missing sequences")
    for name in members:
        model = bundle["models"][name]
        if name == "momentum":
            raw = predict_bundle(model, x, ret=frame["ret_1m"].to_numpy(dtype=float))
        elif name == "lstm":
            raw = predict_bundle(model, sequences)
        else:
            raw = predict_bundle(model, x)
        columns_p.append(apply_calibrator(bundle["calibrators"][name], raw))
    matrix = np.column_stack(columns_p)
    if bundle.get("stacker") is not None and len(members) > 1:
        predicted = bundle["stacker"].predict_proba(matrix)[:, 1]
    elif bundle.get("ensemble_members"):
        predicted = matrix.mean(axis=1)
    else:
        predicted = matrix[:, 0]
    return predicted, y


def _baseline_only(bundle, dev, y_col) -> dict:
    y = dev[y_col].to_numpy(dtype=float)
    return {
        "target": bundle["target"],
        "columns": bundle["columns"],
        "fills": bundle["fills"],
        "models": {"constant": fit_constant(y)},
        "calibrators": {"constant": {"type": "identity"}},
        "primary_model": "constant",
        "ensemble_members": None,
        "stacker": None,
        "threshold": None,
    }


def _shuffled_label_check(dev, columns, y_col, cfg: ExperimentConfig) -> dict:
    ordered = dev.sort_values("entry_epoch")
    cut = int(len(ordered) * 0.7)
    if cut < 20 or len(ordered) - cut < 8:
        return {"status": "skipped", "reason": "development set too small"}
    y = ordered[y_col].to_numpy(dtype=float)
    rng = np.random.default_rng(cfg.seed + 7)
    shuffled = y[:cut].copy()
    rng.shuffle(shuffled)
    if len(np.unique(shuffled)) < 2 or len(np.unique(y[cut:])) < 2:
        return {"status": "skipped", "reason": "one class after the shuffle split"}
    x, fills = design_matrix(ordered.iloc[:cut], columns)
    x_val, _ = design_matrix(ordered.iloc[cut:], columns, fills)
    predicted = predict_bundle(fit_logistic(x, shuffled, cfg.seed), x_val)
    auc = safe_auc(y[cut:], predicted)
    return {
        "status": "ok",
        "shuffled_validation_auc": auc,
        "warning": bool(auc is not None and auc > 0.65),
        "note": "Train labels were shuffled. A high validation AUC suggests a pipeline error.",
    }


def _payout(quotes, dev_epochs, target, cfg: ExperimentConfig) -> dict:
    matched = matched_net_profit_r(
        quotes,
        dev_epochs,
        target=target,
        horizon_seconds=cfg.horizon_seconds,
        barrier_distance=cfg.barrier_distance,
    )
    if matched.get("r") is not None:
        matched["breakeven_probability"] = breakeven_probability(matched["r"])
        return matched
    if cfg.hypothetical_r is not None:
        return {
            "source": "hypothetical",
            "r": float(cfg.hypothetical_r),
            "breakeven_probability": breakeven_probability(cfg.hypothetical_r),
            "note": "User-supplied hypothetical payout. This is not a historical quote and it is not verified profit.",
        }
    return {
        "source": "unavailable",
        "r": None,
        "breakeven_probability": None,
        "note": "No quote file was matched. Predictive metrics are reported without a profitability claim.",
    }


def _try_stack(early, late, names, calibrators):
    early_y = early["y"].to_numpy().astype(int)
    if len(np.unique(early_y)) < 2:
        return None, None
    from sklearn.linear_model import LogisticRegression

    early_x = np.column_stack([apply_calibrator(calibrators[name], early[name].to_numpy()) for name in names])
    late_x = np.column_stack([apply_calibrator(calibrators[name], late[name].to_numpy()) for name in names])
    model = LogisticRegression(max_iter=200)
    model.fit(early_x, early_y)
    loss = binary_logloss(late["y"].to_numpy(), model.predict_proba(late_x)[:, 1])
    return model, loss


def _time_halves(oof: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, bool]:
    cut = float(np.quantile(oof["entry_epoch"], 0.5))
    early = oof.loc[oof["entry_epoch"] < cut].reset_index(drop=True)
    late = oof.loc[oof["entry_epoch"] >= cut].reset_index(drop=True)
    if len(early) < 8 or len(late) < 8:
        return oof, oof, True
    return early, late, False


def _model_logloss(frame: pd.DataFrame, name: str):
    if name not in frame.columns:
        return None
    return binary_logloss(frame["y"].to_numpy(), frame[name].to_numpy())


def _aligned_logloss(oof: pd.DataFrame, epochs: np.ndarray, model: str):
    if model not in oof.columns:
        return None
    table = oof.set_index("entry_epoch")
    rows = []
    probs = []
    for epoch in epochs:
        if epoch not in table.index:
            continue
        rows.append(float(table.loc[epoch, "y"] if np.ndim(table.loc[epoch, "y"]) == 0 else table.loc[epoch, "y"].iloc[0]))
        value = table.loc[epoch, model]
        probs.append(float(value if np.ndim(value) == 0 else value.iloc[0]))
    if len(rows) < 8:
        return None
    return binary_logloss(np.asarray(rows), np.asarray(probs))


def _financial_note(payout: dict, test_metrics: dict) -> dict:
    status = payout.get("source", "unavailable")
    note = payout.get("note")
    if status != "historical_quotes":
        note = (
            "Predictive metrics only. No verified profitability. "
            + str(note or "")
        )
    return {
        "status": status,
        "r": payout.get("r"),
        "breakeven_probability": payout.get("breakeven_probability"),
        "formula": "expected_return_per_stake = p * r - (1 - p); break_even = 1 / (1 + r)",
        "test_hypothetical_pnl": test_metrics.get("hypothetical_pnl_per_opportunity"),
        "note": note,
    }


def _public_selection(frozen: dict) -> dict:
    return {
        "feature_group": frozen["feature_group"],
        "model": frozen["model"],
        "ensemble": frozen["ensemble"],
        "threshold": frozen["threshold"],
        "tree_depths": frozen["tree_depths"],
        "feature_ablation": frozen["feature_ablation"],
        "shuffled_label_check": frozen["shuffled_label_check"],
        "validation_logloss": frozen["validation_logloss"],
        "validation_auc": frozen["validation_auc"],
        "baseline_logloss": frozen["baseline_logloss"],
        "lstm": frozen["lstm"],
        "shared_validation_limitation": frozen["shared_validation_limitation"],
        "payout": frozen["payout"],
    }


def _leak_asof(tables, cfg: ExperimentConfig):
    minute = tables["m1"]
    epochs = tables["epochs"]
    if minute.empty:
        return None
    start = max(0, len(minute) // 2)
    for close_time in minute["close_time"].iloc[start:].astype(int):
        stamp = int(close_time)
        if not np.any(epochs > stamp):
            continue
        if lookup_features(tables, stamp, cfg) is not None:
            return stamp
    return None


def _stopped(target: str, reason: str) -> dict:
    return {
        "target": target,
        "test_opened": False,
        "labeled_examples": 0,
        "approximate_independent_outcomes": 0,
        "limitation": reason,
    }


def _write_report(path: Path, report: dict) -> None:
    payload = _jsonable(report)
    (path / "report.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    write_markdown(path / "report.md", payload)


def _jsonable(value):
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if np.isfinite(number) else None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    return value
