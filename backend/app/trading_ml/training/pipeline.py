"""Chronological R_100 evaluation. The untouched test chooses nothing."""
from __future__ import annotations

import time

import numpy as np
import pandas as pd
from sklearn.metrics import log_loss

from app.trading_ml.backtesting.contracts import expected_value, settle_contract
from app.trading_ml.config import (
    DEFAULT_DURATION_SECONDS,
    LIMITATIONS,
    MIN_LABELED_ROWS,
    STEP_SECONDS,
    validate_delay,
    validate_duration,
    validate_payout,
)
from app.trading_ml.data.quality import assess_candles
from app.trading_ml.evaluation.metrics import (
    classification_metrics,
    clip_probability,
    expected_value_bound,
    max_drawdown,
    wilson_interval,
)
from app.trading_ml.features.causal import FEATURE_COLUMNS, contract_labels, feature_frame
from app.trading_ml.models.calibration import PlattCalibrator
from app.trading_ml.models.direction import fit_estimator, positive_probability, resolve_models
from app.trading_ml.models.regime import hmm_report
from app.trading_ml.models.roster import publish_roster
from app.trading_ml.models.sequence import run_sequence_benchmark, sequence_status
from app.trading_ml.models.volatility import forecast_volatility
from app.trading_ml.monitoring.approval import judge_approval
from app.trading_ml.research.patterns import historical_neighbors, structure_report
from app.trading_ml.research.strategy import choose_strategy
from app.trading_ml.risk.limits import ev_floor
from app.trading_ml.signals.engine import decide_signal
from app.trading_ml.training.search import search_training_params
from app.trading_ml.training.splits import embargo_splits


def run_evaluation(candles: list[dict], *, duration_seconds: int = DEFAULT_DURATION_SECONDS,
                   entry_delay_seconds: int = 0, payout: float | None = None,
                   limits: dict | None = None, models: list[str] | None = None,
                   paper: dict | None = None, extra_risk: list[str] | None = None,
                   now: int | None = None, progress: dict | None = None) -> tuple[dict, dict]:
    """Return a JSON report and a model bundle. The bundle is not itself a performance claim."""
    duration = validate_duration(duration_seconds)
    delay = validate_delay(entry_delay_seconds)
    payout = validate_payout(payout)
    limits = dict(limits or {})
    from app.trading_ml.config import DEFAULT_LIMITS
    for key, value in DEFAULT_LIMITS.items():
        limits.setdefault(key, value)
    stamp = int(time.time()) if now is None else int(now)
    names, notes = resolve_models(models)

    def note(message: str) -> None:
        if progress is not None:
            progress["progress"] = message

    note("Checking R_100 candles")
    quality = assess_candles(candles, STEP_SECONDS, now=stamp)
    cleaned = quality.pop("candles")
    if len(cleaned) < MIN_LABELED_ROWS:
        raise ValueError("Not enough valid closed R_100 candles. Missing history was not invented.")
    note("Building causal features and contract labels")
    features = feature_frame(cleaned, STEP_SECONDS)
    labels = contract_labels(cleaned, duration=duration, entry_delay=delay, step=STEP_SECONDS)
    if labels.empty:
        raise ValueError("No contract had a complete entry-to-expiry path. Outcomes were not invented.")
    merged = features.merge(labels, on=["epoch", "decision_epoch"], how="left")
    finite = features.dropna(subset=FEATURE_COLUMNS)
    if finite.empty:
        raise ValueError("No causal feature row was finite. Missing values were not filled.")
    live = finite.iloc[-1]
    pool = merged.dropna(subset=FEATURE_COLUMNS)
    pool = pool[pool["decision_epoch"] < int(live["decision_epoch"])]
    pool = pool[pool["expiry_epoch"].notna() & (pool["expiry_epoch"] < int(live["decision_epoch"]))]
    model_rows = pool[pool["tie"] == False].dropna(subset=["label"]).copy()
    model_rows["label"] = model_rows["label"].astype(int)
    model_rows = model_rows.sort_values("decision_epoch").reset_index(drop=True)
    if len(model_rows) < MIN_LABELED_ROWS:
        raise ValueError(
            f"Only {len(model_rows)} labeled contracts are usable. "
            f"At least {MIN_LABELED_ROWS} are required, and none were invented."
        )
    note("Running purged walk-forward")
    splits, development, test = embargo_splits(model_rows, duration)
    chosen_params: dict[str, dict] = {}
    earliest = splits[0][0]
    for name in names:
        chosen_params[name] = search_training_params(
            name,
            earliest[FEATURE_COLUMNS].to_numpy(dtype=float),
            earliest["label"].to_numpy(dtype=int),
        )
        if chosen_params[name]:
            notes.append(
                f"{name} settings were chosen on the earliest training rows only: {chosen_params[name]}"
            )
    fold_loss = {name: [] for name in names}
    held_out = {name: [] for name in names}
    for fold, (train, validation) in enumerate(splits, start=1):
        note(f"Walk-forward fold {fold}")
        x_train = train[FEATURE_COLUMNS].to_numpy(dtype=float)
        y_train = train["label"].to_numpy(dtype=int)
        x_validation = validation[FEATURE_COLUMNS].to_numpy(dtype=float)
        y_validation = validation["label"].to_numpy(dtype=int)
        for name in names:
            try:
                estimator = fit_estimator(name, x_train, y_train, chosen_params.get(name))
                probability = positive_probability(estimator, x_validation)
                fold_loss[name].append(float(log_loss(y_validation, clip_probability(probability), labels=[0, 1])))
                held_out[name].append(pd.DataFrame({
                    "decision_epoch": validation["decision_epoch"].to_numpy(),
                    "label": y_validation,
                    "probability": probability,
                }))
            except Exception as exc:
                fold_loss[name].append(None)
                notes.append(f"{name} failed on fold {fold}: {type(exc).__name__}: {exc}")
    eligible = [name for name in names if fold_loss[name] and all(item is not None for item in fold_loss[name])]
    if not eligible:
        raise ValueError("Every direction model failed walk-forward. No score was invented.")
    means = {name: float(np.mean(fold_loss[name])) for name in eligible}
    selected = min(eligible, key=lambda name: (means[name], eligible.index(name)))
    selected_loss = means[selected]
    ensemble = False
    if len(eligible) >= 2:
        combined = None
        for name in eligible:
            part = pd.concat(held_out[name], ignore_index=True)
            part = part.rename(columns={"probability": name})[["decision_epoch", "label", name]]
            combined = part if combined is None else combined.merge(part.drop(columns=["label"]), on="decision_epoch")
        average = combined[eligible].mean(axis=1)
        ensemble_loss = float(log_loss(combined["label"], clip_probability(average), labels=[0, 1]))
        if ensemble_loss + 0.001 < selected_loss:
            selected = "ensemble"
            selected_loss = ensemble_loss
            ensemble = True
    note("Fitting the selected model before the untouched test")
    members = [selected] if selected != "ensemble" else eligible
    development = development.sort_values("decision_epoch").reset_index(drop=True)
    cut = max(int(len(development) * 0.85), len(development) - max(30, int(len(development) * 0.15)))
    cut = min(max(cut, 20), len(development) - 20)
    calibration = development.iloc[cut:].copy()
    fit_rows = development.iloc[:cut].copy()
    calibration_start = int(calibration["decision_epoch"].min())
    fit_rows = fit_rows[fit_rows["expiry_epoch"] < calibration_start]
    fit_rows = fit_rows[fit_rows["decision_epoch"] < calibration_start - duration]
    if len(fit_rows) < 30 or len(calibration) < 20:
        raise ValueError("The calibration split does not have enough earlier rows.")
    fitted = {}
    member_probability = []
    comparison = []
    y_test = test["label"].to_numpy(dtype=int)
    x_test = test[FEATURE_COLUMNS].to_numpy(dtype=float)
    for name in members:
        estimator = fit_estimator(
            name,
            fit_rows[FEATURE_COLUMNS].to_numpy(dtype=float),
            fit_rows["label"].to_numpy(dtype=int),
            chosen_params.get(name),
        )
        calibrator = PlattCalibrator().fit(
            positive_probability(estimator, calibration[FEATURE_COLUMNS].to_numpy(dtype=float)),
            calibration["label"].to_numpy(dtype=int),
        )
        raw = positive_probability(estimator, x_test)
        adjusted = calibrator.transform(raw)
        fitted[name] = {"model": estimator, "calibrator": calibrator}
        member_probability.append(adjusted)
        metrics = classification_metrics(y_test, adjusted)
        comparison.append({
            "name": name, "validation_log_loss": means.get(name),
            "test_log_loss": metrics["log_loss"], "test_auc": metrics["auc"],
            "test_brier": metrics["brier"], "error": None,
        })
    for name in names:
        if name not in members:
            failure = next((item for item in notes if item.startswith(f"{name} failed")), None)
            comparison.append({
                "name": name, "validation_log_loss": means.get(name),
                "test_log_loss": None, "test_auc": None, "test_brier": None, "error": failure,
            })
    probability = np.mean(member_probability, axis=0)
    calibrated = all(item["calibrator"].fitted for item in fitted.values())
    base_rate = float(np.clip(fit_rows["label"].mean(), 1e-6, 1 - 1e-6))
    baseline = classification_metrics(y_test, np.full(len(y_test), base_rate))
    window = pool[(pool["decision_epoch"] >= int(fit_rows["decision_epoch"].min()))
                  & (pool["decision_epoch"] <= int(fit_rows["decision_epoch"].max()))]
    tie_rate = float(window["tie"].mean()) if len(window) else 0.0
    selected_trades = _select(test, probability, tie_rate, payout, limits)
    wins = sum(1 for row in selected_trades if row["outcome"] == "win")
    profits = [row["profit_per_stake"] for row in selected_trades]
    lower, upper = wilson_interval(wins, len(selected_trades))
    metrics = classification_metrics(y_test, probability)
    test_report = {
        **metrics,
        "baseline_log_loss": baseline["log_loss"],
        "baseline_brier": baseline["brier"],
        "baseline_accuracy": baseline["accuracy"],
        "calibrated": calibrated,
        "selected": len(selected_trades),
        "wins": wins,
        "losses": len(selected_trades) - wins,
        "win_rate": None if not selected_trades else wins / len(selected_trades),
        "wilson_lower": lower,
        "wilson_upper": upper,
        "ev_wilson_lower": expected_value_bound(lower, payout),
        "expected_profit_per_stake": None if not profits or any(item is None for item in profits) else float(np.mean(profits)),
        "max_drawdown": max_drawdown([float(item) for item in profits]) if profits and all(item is not None for item in profits) else None,
    }
    approval = judge_approval(test_report, payout=payout, min_selected=int(limits["min_historical_signals"]))
    note("Scoring the latest closed candle")
    live_probability = _live_probability(fitted, live)
    rise = live_probability * (1 - tie_rate)
    candidate = "RISE" if rise >= (1 - live_probability) * (1 - tie_rate) else "FALL"
    pattern = historical_neighbors(
        development[FEATURE_COLUMNS].to_numpy(dtype=float),
        development["label"].to_numpy(dtype=int),
        live[FEATURE_COLUMNS].to_numpy(dtype=float),
        candidate=candidate, payout=payout,
    )
    strategy = choose_strategy(live)
    volatility = forecast_volatility(features, set(int(epoch) for epoch in fit_rows["epoch"]), int(live["epoch"]))
    returns = features.loc[features["epoch"] <= int(live["epoch"]), "ret_1"].dropna().to_numpy(dtype=float)
    train_returns = features.loc[features["epoch"].isin(set(fit_rows["epoch"])), "ret_1"].dropna().to_numpy(dtype=float)
    sequence_rows = development.dropna(subset=["ret_1", "label"]).tail(400)
    if len(sequence_rows) >= 40:
        sequence = run_sequence_benchmark(
            sequence_rows["ret_1"].to_numpy(dtype=float),
            sequence_rows["label"].to_numpy(dtype=int),
        )
    else:
        sequence = sequence_status()
    structures = structure_report(development)
    regime_hmm = hmm_report(train_returns, returns)
    roster = publish_roster(
        comparison, notes, sequence, regime_hmm, pattern, selected=selected,
    )
    from app.trading_ml.risk.limits import risk_blockers
    blockers = risk_blockers(
        limits=limits, paper=paper or {}, trained_at=stamp, now=stamp,
        test_selected=len(selected_trades), extra=extra_risk,
    )
    card = decide_signal(
        probability_up=live_probability, calibrated=calibrated, tie_rate=tie_rate,
        strategy=strategy, pattern=pattern, volatility=volatility, payout=payout,
        approval_state=approval["state"], quality_blocks=bool(quality["blocks_signal"]),
        quality_reason=quality["reason"], risk_blockers=blockers, duration_seconds=duration,
        model_name=selected, ev_floor=ev_floor(limits),
    )
    card["decision_epoch"] = int(live["decision_epoch"])
    report = {
        "symbol": "R_100", "contract": "Rise/Fall", "duration_seconds": duration,
        "entry_delay_seconds": delay, "trained_at": stamp, "payout": payout,
        "payout_source": None if payout is None else "user_supplied",
        "selected_model": selected, "ensemble": ensemble, "approval": approval,
        "approval_state": approval["state"], "signal": card, "test": test_report,
        "models": roster, "notes": notes, "data_quality": quality,
        "dataset": {
            "rows": int(len(model_rows)), "ties": int(pool["tie"].sum()), "tie_rate": tie_rate,
            "decision_start": int(model_rows["decision_epoch"].min()),
            "decision_end": int(model_rows["decision_epoch"].max()),
        },
        "backtest": {
            "source": "backtest", "signals": len(selected_trades), "wins": wins,
            "losses": len(selected_trades) - wins, "win_rate": test_report["win_rate"],
            "expected_profit_per_stake": test_report["expected_profit_per_stake"],
            "max_drawdown": test_report["max_drawdown"], "payout": payout,
            "payout_source": None if payout is None else "user_supplied",
            "note": "Candle-path backtest of the untouched test. This is not the paper-trading journal.",
        },
        "optional_research": {
            "sequence": sequence,
            "hmm": regime_hmm,
            "structures": structures,
            "hyperparameters": chosen_params,
        },
        "limitations": list(LIMITATIONS), "places_trades": False,
        "probability_note": "Probabilities are model estimates from past R_100 candles, not a guaranteed win rate.",
    }
    bundle = {
        "members": fitted, "feature_columns": list(FEATURE_COLUMNS), "tie_rate": tie_rate,
        "selected_model": selected, "trained_at": stamp, "duration_seconds": duration,
        "entry_delay_seconds": delay,
    }
    return report, bundle


def _select(frame: pd.DataFrame, probability: np.ndarray, tie_rate: float,
            payout: float | None, limits: dict) -> list[dict]:
    floor = ev_floor(limits)
    chosen = []
    for index, (_, row) in enumerate(frame.iterrows()):
        rise = float(probability[index]) * (1 - tie_rate)
        fall = (1 - float(probability[index])) * (1 - tie_rate)
        side = "RISE" if rise >= fall else "FALL"
        chance = rise if side == "RISE" else fall
        strategy = choose_strategy(row)
        value = expected_value(chance, payout)
        if strategy.get("direction") != side or not strategy.get("chosen"):
            continue
        if value is None or value < floor:
            continue
        settled = settle_contract(side, float(row["entry_price"]), float(row["exit_price"]), payout)
        chosen.append({**settled, "probability": chance, "expected_value": value})
    return chosen


def score_bundle(bundle: dict, candles: list[dict], *, report: dict, payout: float | None,
                 limits: dict, paper: dict | None = None, extra_risk: list[str] | None = None,
                 now: int | None = None) -> dict:
    """Score the latest closed candle with the saved model. This does not retrain or reapprove."""
    stamp = int(time.time()) if now is None else int(now)
    duration = int(bundle["duration_seconds"])
    if int(report.get("duration_seconds") or 0) != duration:
        raise ValueError("The saved research report does not match the saved model duration.")
    saved_payout = report.get("payout")
    if payout is None:
        payout = saved_payout
    payout = validate_payout(payout)
    if payout != saved_payout:
        raise ValueError("Re-run research evaluation to test a different payout. Scoring will not apply an untested payout.")
    quality = assess_candles(candles, STEP_SECONDS, now=stamp)
    cleaned = quality.pop("candles")
    features = feature_frame(cleaned, STEP_SECONDS)
    finite = features.dropna(subset=FEATURE_COLUMNS)
    if finite.empty:
        raise ValueError("No finite feature row is available for the latest candle.")
    live = finite.iloc[-1]
    live_probability = _live_probability(bundle["members"], live)
    tie_rate = float(bundle["tie_rate"])
    rise = live_probability * (1 - tie_rate)
    candidate = "RISE" if rise >= (1 - live_probability) * (1 - tie_rate) else "FALL"
    # Neighbors stay as recorded on the evaluation. Rebuilding them here would need the
    # original training matrix, which is not stored. Absence does not count as support.
    pattern = {"available": False, "supports_candidate": False, "opposes_candidate": False,
               "reason": "Historical neighbors were not recomputed during scoring."}
    strategy = choose_strategy(live)
    earlier = features.loc[features["epoch"] < int(live["epoch"]), "epoch"]
    volatility = forecast_volatility(features, set(int(epoch) for epoch in earlier), int(live["epoch"]))
    from app.trading_ml.risk.limits import risk_blockers
    blockers = risk_blockers(
        limits=limits, paper=paper or {}, trained_at=bundle.get("trained_at"), now=stamp,
        test_selected=int(report.get("test", {}).get("selected") or 0), extra=extra_risk,
    )
    card = decide_signal(
        probability_up=live_probability, calibrated=bool(report.get("test", {}).get("calibrated")),
        tie_rate=tie_rate, strategy=strategy, pattern=pattern, volatility=volatility, payout=payout,
        approval_state=report.get("approval_state") or "RESEARCH",
        quality_blocks=bool(quality["blocks_signal"]), quality_reason=quality["reason"],
        risk_blockers=blockers, duration_seconds=duration, model_name=bundle.get("selected_model"),
        ev_floor=ev_floor(limits),
    )
    card["decision_epoch"] = int(live["decision_epoch"])
    return card


def _live_probability(fitted: dict, live: pd.Series) -> float:
    values = live[FEATURE_COLUMNS].to_numpy(dtype=float).reshape(1, -1)
    parts = []
    for member in fitted.values():
        raw = positive_probability(member["model"], values)
        parts.append(float(member["calibrator"].transform(raw)[0]))
    return float(np.mean(parts))
