"""Train the requested technical profile and benchmark it on a shared decision grid.

The selected technical candidate is frozen on development data. The final period
has been used by earlier experiments and is explicitly diagnostic, not fresh proof.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import pickle

import numpy as np
import pyarrow.parquet as pq
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .core import (BARRIER, COLOMBO_OFFSET, DELAY, DURATION, SCHEMA, SYMBOL,
                   features, side_matrix, touch_labels, validate_ticks)
from .training import Predictor, json_save, logit, summarize

PROFILE = "technical_v2"
THRESHOLDS = [.95, .96, .97, .975, .98, .985, .99]


def select_policy(trials):
    """Selection depends solely on earlier development outcomes and coverage."""
    eligible = [row for row in trials if row["signals"] >= 100
                and 10 <= row["signals_per_full_day"] <= 15
                and row["full_day_cluster_ci95"]]
    if not eligible:
        raise ValueError("No technical candidate meets the requested signal coverage at >=95% estimated confidence")
    return min(eligible, key=lambda row: (-row["full_day_cluster_ci95"][0], row["brier"],
                                          row["model"], -row["threshold"]))


def feature_groups(names):
    from .technical import FEATURE_GROUPS
    groups = {}
    for title, specification in FEATURE_GROUPS.items():
        if isinstance(specification, str):
            matches = [name for name in names if name.startswith(specification)]
        else:
            matches = [name for name in names if name.startswith(tuple(specification))]
        if not matches:
            raise ValueError(f"Missing technical feature family: {title}")
        groups[title] = matches
    if len(groups) != 9:
        raise ValueError("The requested technical profile requires all nine feature families")
    return groups


def fit_candidate(name, names, x, y, masks, profile):
    if name == "logistic":
        estimator = make_pipeline(StandardScaler(), LogisticRegression(C=.1, max_iter=1000))
    elif name == "xgboost":
        from xgboost import XGBClassifier
        estimator = XGBClassifier(objective="binary:logistic", n_estimators=80, max_depth=2,
            learning_rate=.05, min_child_weight=20, reg_lambda=10, n_jobs=1,
            random_state=20261018, tree_method="hist", verbosity=0)
    elif name == "catboost":
        from catboost import CatBoostClassifier
        estimator = CatBoostClassifier(iterations=80, depth=3, learning_rate=.05, l2_leaf_reg=10,
            loss_function="Logloss", thread_count=1, task_type="CPU", random_seed=20261018,
            allow_writing_files=False, verbose=False)
    else:
        raise ValueError("Unknown model")
    fit = masks["fit"]; calibration = masks["calibration"]
    estimator.fit(side_matrix(x[fit]), y[fit].T.ravel())
    raw = estimator.predict_proba(side_matrix(x[calibration]))[:, 1]
    calibrator = LogisticRegression(C=1, max_iter=500).fit(logit(raw), y[calibration].T.ravel())
    return Predictor(name, names, estimator=estimator, calibrator=calibrator, feature_profile=profile)


def selection_trials(models, matrices, d, y, masks, bounds):
    dev = masks["selection"]
    trials = []
    for name, model in models.items():
        q = model.predict(matrices[name][dev])
        brier = float(brier_score_loss(y[dev].ravel(), q.ravel()))
        for threshold in THRESHOLDS:
            stats = summarize(d[dev], y[dev], q, threshold, *bounds["selection"])
            trials.append({"model": name, "brier": brier, **stats})
    return trials


def metrics(model, x, d, y, mask, threshold, bounds):
    q = model.predict(x[mask]); target = y[mask].ravel()
    chosen = np.argmax(q, axis=1)
    rows = np.arange(len(chosen))
    return {
        "brier": float(brier_score_loss(target, q.ravel())),
        "auc": float(roc_auc_score(target, q.ravel())),
        "chosen_direction_brier": float(brier_score_loss(y[mask][rows, chosen], q[rows, chosen])),
        "decisions": int(mask.sum()),
        "policy": summarize(d[mask], y[mask], q, threshold, *bounds),
    }


def group_importance(model, groups):
    """Descriptive model weights, not independent votes or measured accuracy gains."""
    weights = getattr(model.estimator, "feature_importances_", None)
    if weights is None:
        return None
    weights = np.asarray(weights, dtype=float)
    n = len(model.feature_names)
    if len(weights) != 2 * n + 1:
        return None
    base = weights[:n] + weights[n + 1:]
    total = float(weights.sum()) or 1.
    by_name = dict(zip(model.feature_names, base.tolist()))
    return {title: float(sum(by_name[name] for name in names) / total)
            for title, names in groups.items()}


def train_technical(source: Path, output: Path, *, activate=True):
    from .technical import HISTORY_SECONDS, TechnicalContext

    source, output = source.resolve(), output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    run = output / run_id; run.mkdir()
    plan = {
        "schema": SCHEMA, "feature_profile": PROFILE, "symbol": SYMBOL,
        "barrier_points": BARRIER, "duration_seconds": DURATION,
        "processing_delay_seconds": DELAY, "history_seconds": HISTORY_SECONDS,
        "split": "45% fit / 15% calibration / 20% selection / 20% diagnostic test",
        "source": source.name, "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "feature_definition_sha256": hashlib.sha256(Path(__file__).with_name("technical.py").read_bytes()).hexdigest(),
        "status": "exploratory reused-history comparison; fresh forward validation required",
        "models": ["logistic", "xgboost", "catboost"],
        "controls": ["frequency", "compact/logistic", "compact/xgboost", "compact/catboost"],
        "calibration": "independent held-out Platt calibration for every fitted candidate",
        "thresholds": THRESHOLDS,
        "selection": "best technical learned candidate on selection-period day-cluster lower touch bound; >=100 signals and 10–15/full day; tie lower Brier then model name",
        "control_policy": "compact controls use the identical eligible timestamps; control winner also frozen on selection only",
        "activation": "requested technical profile, subject to coverage and probability gates; benchmark does not establish improved live accuracy",
        "cap": 15, "cooldown_seconds": 5760, "candidate_spacing_seconds": 600,
        "objective": "touch accuracy; no payout or trade execution dependency",
    }
    json_save(run / "protocol.json", plan)
    print(f"Run {run_id}: building causal technical features", flush=True)
    table = pq.read_table(source)
    if "symbol" in table.column_names and set(table["symbol"].to_pylist()) != {SYMBOL}:
        raise ValueError("This profile accepts an R_100-only tick file")
    t, p = validate_ticks(np.asarray(table["epoch"]), np.asarray(table["quote"]))
    del table
    lo, hi = int(t[0]), int(t[-1])
    cuts = [lo] + [lo + int(f * (hi - lo)) for f in (.45, .6, .8)] + [hi]
    bounds = dict(zip(("fit", "calibration", "selection", "test"), zip(cuts[:-1], cuts[1:])))
    context = TechnicalContext(t, p)
    first = ((lo + 900 + COLOMBO_OFFSET + 599) // 600) * 600 - COLOMBO_OFFSET
    records = []; compact_records = []; names = None; compact_names = None
    attempts = 0
    for epoch in range(first, hi - DELAY - DURATION, 600):
        attempts += 1
        row = features(t, p, epoch); target = touch_labels(t, p, epoch)
        if row is None or target is None:
            continue
        compact_records.append((epoch, list(row.values()), target))
        technical = context.snapshot(epoch)
        if technical is not None:
            compact_names = list(row)
            row.update(technical)
            if names is not None and names != list(row):
                raise ValueError("Technical feature column order changed within the dataset")
            names = list(row)
            records.append((epoch, list(row.values()), target))
        if attempts % 2000 == 0:
            print(f"Feature decisions: {attempts}; technical eligible: {len(records)}", flush=True)
    if not records:
        raise ValueError("No complete technical windows; collect more continuous history")
    d = np.array([r[0] for r in records]); x = np.array([r[1] for r in records]); y = np.array([r[2] for r in records])
    compact_x = x[:, :len(compact_names)]
    native_d = np.array([r[0] for r in compact_records])
    native_x = np.array([r[1] for r in compact_records]); native_y = np.array([r[2] for r in compact_records])
    masks = {name: (d >= a) & (d + DELAY + DURATION < b) for name, (a, b) in bounds.items()}
    native_masks = {name: (native_d >= a) & (native_d + DELAY + DURATION < b) for name, (a, b) in bounds.items()}
    for name, mask in masks.items():
        if mask.sum() < 200 or len(np.unique(y[mask])) < 2:
            raise ValueError(f"Need >=200 decisions and both outcomes in {name}")
    groups = feature_groups(names)
    np.savez_compressed(run / "decisions.npz", epoch=d, features=x, labels=y, feature_names=np.asarray(names))
    coverage = {phase: {"compact_eligible_decisions": int(native_masks[phase].sum()),
                        "technical_eligible_decisions": int(mask.sum()),
                        "additional_history_abstentions": int(native_masks[phase].sum() - mask.sum())}
                for phase, mask in masks.items()}
    models = {}; matrices = {}; controls = {}; control_matrices = {}; errors = {}
    controls["frequency"] = Predictor("frequency", compact_names, tuple(y[masks["calibration"]].mean(axis=0)))
    control_matrices["frequency"] = compact_x
    for profile, matrix, columns in (("compact_v1", compact_x, compact_names), (PROFILE, x, names)):
        for name in plan["models"]:
            key = name if profile == PROFILE else f"compact/{name}"
            try:
                candidate = fit_candidate(name, columns, matrix, y, masks, profile)
                if profile == PROFILE:
                    models[key] = candidate; matrices[key] = matrix
                else:
                    controls[key] = candidate; control_matrices[key] = matrix
                print(f"Fitted {key}: {len(columns)} features", flush=True)
            except (ImportError, ValueError, RuntimeError) as exc:
                errors[key] = str(exc)
    trials = selection_trials(models, matrices, d, y, masks, bounds)
    control_trials = selection_trials(controls, control_matrices, d, y, masks, bounds)
    json_save(run / "trials.json", {"technical": trials, "controls": control_trials})
    try:
        selected = select_policy(trials)
    except ValueError:
        json_save(run / "no_policy.json", {"reason": "No expanded learned policy met coverage", "coverage": coverage, "errors": errors})
        raise
    try:
        control_selected = select_policy(control_trials)
    except ValueError:
        control_selected = None
    # Frozen before reading the diagnostic test outcomes for any model.
    json_save(run / "selection.json", {**selected, "control_selected": control_selected})
    comparisons = {}
    all_models = {**controls, **models}; all_matrices = {**control_matrices, **matrices}
    for key, candidate in all_models.items():
        comparisons[key] = {"feature_profile": getattr(candidate, "feature_profile", "compact_v1")}
        for phase in ("selection", "test"):
            comparisons[key][phase] = metrics(candidate, all_matrices[key], d, y, masks[phase], selected["threshold"], bounds[phase])
    control_benchmark = None
    if control_selected:
        key = control_selected["model"]; control = controls[key]
        control_benchmark = {
            "selection": control_selected,
            "common_grid_test": metrics(control, compact_x, d, y, masks["test"], control_selected["threshold"], bounds["test"]),
            "native_coverage_test": metrics(control, native_x, native_d, native_y, native_masks["test"], control_selected["threshold"], bounds["test"]),
        }
    model = models[selected["model"]]
    report = {**plan, "run_id": run_id, "ticks": len(t), "span_days": (hi - lo) / 86400,
        "feature_names": names, "feature_groups": groups, "feature_count": len(names),
        "side_feature_count": 2 * len(names) + 1, "feature_group_importance": group_importance(model, groups),
        "split_counts": {k: int(v.sum()) for k, v in masks.items()}, "bounds": bounds, "coverage": coverage,
        "selection": selected, "comparison": comparisons, "control_benchmark": control_benchmark,
        "model_errors": errors, "test": comparisons[selected["model"]]["test"],
        "forward_validation": "pending", "live_accuracy_improvement_demonstrated": False,
        "liquidity_note": "Price-sweep proxies only; no order-book or stop-order observations",
    }
    artifact = {"schema": SCHEMA, "feature_profile": PROFILE, "predictor": model,
                "threshold": selected["threshold"], "report": report}
    with (run / "bundle.pkl").open("wb") as stream:
        pickle.dump(artifact, stream)
    json_save(run / "report.json", report)
    if activate:
        json_save(output / "latest.json", {"run_id": run_id})
    print(f"Saved {run_id}; selected {selected['model']}; technical inputs {len(names)}", flush=True)
    print(f"Diagnostic test: {report['test']['policy']}", flush=True)
    return report
