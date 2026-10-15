"""Demo inference. Uses the same feature lookup as training and never places an order."""

from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from app.research_pipeline.features import build_tables, lookup_features, sequence_matrix
from app.research_pipeline.labels import label_entries
from app.research_pipeline.modeling import apply_calibrator, predict_bundle
from app.research_pipeline.settings import ExperimentConfig, load_config


def load_bundle(path: str | Path) -> dict:
    bundle_path = Path(path)
    if bundle_path.is_dir():
        bundle_path = bundle_path / "model.joblib"
    return joblib.load(bundle_path)


def config_from_bundle(bundle: dict) -> ExperimentConfig:
    stored = dict(bundle.get("config") or {})
    stored.pop("defaults_are_confirmed_trading_settings", None)
    return load_config(**stored)


def score_asof(
    ticks: pd.DataFrame,
    bundle: dict,
    asof: int,
    *,
    cfg: ExperimentConfig | None = None,
) -> dict:
    cfg = cfg or config_from_bundle(bundle)
    clean_epochs, clean_quotes, prepared = _prepare_arrays(ticks, cfg)
    version = str(bundle.get("version", "unknown"))
    quality = prepared if prepared != "ok" else _quality_at(clean_epochs, int(asof), cfg.max_gap_seconds)
    base = {
        "timestamp": int(asof),
        "instrument": cfg.symbol,
        "target": bundle.get("target"),
        "horizon_seconds": int(cfg.horizon_seconds),
        "barrier_distance": float(cfg.barrier_distance) if bundle.get("target") != "direction" else None,
        "barrier_unit": cfg.barrier_unit if bundle.get("target") != "direction" else None,
        "calibrated_probability": None,
        "model_version": version,
        "data_quality": quality,
        "decision": "no_signal",
        "reason": "",
        "order_placed": False,
        "experimental": not bool(cfg.confirmed_trading_settings),
    }
    if quality != "ok":
        base["reason"] = f"data quality is {quality}"
        return base
    tables = build_tables(clean_epochs, clean_quotes, cfg)
    features = lookup_features(tables, int(asof), cfg)
    if features is None:
        base["data_quality"] = "incomplete"
        base["reason"] = "completed-candle features are not available at this timestamp"
        return base
    columns = list(bundle["columns"])
    if any(not np.isfinite(features.get(name, np.nan)) for name in columns):
        base["data_quality"] = "incomplete"
        base["reason"] = "feature vector is incomplete at this timestamp"
        return base
    row = pd.DataFrame([{name: features[name] for name in columns}])
    row["entry_epoch"] = int(asof)
    if "ret_1m" not in row.columns:
        row["ret_1m"] = features.get("ret_1m", np.nan)
    probability = _combined_probability(bundle, row, clean_epochs, clean_quotes, cfg)
    base["calibrated_probability"] = float(probability)
    threshold = bundle.get("threshold")
    if threshold is None:
        base["reason"] = "no validation threshold was selected; probability is informational and no demo signal is emitted"
        return base
    if float(probability) >= float(threshold):
        base["decision"] = "signal"
        base["reason"] = (
            "calibrated probability is at or above the validation threshold; "
            "demo only, no order placed"
        )
    else:
        base["reason"] = "calibrated probability is below the validation threshold"
    return base


def score_batch(ticks: pd.DataFrame, bundle: dict, cfg: ExperimentConfig | None = None) -> list[dict]:
    """Score every training-grid timestamp that has features. Does not place orders."""
    cfg = cfg or config_from_bundle(bundle)
    from app.research_pipeline.labels import decision_times
    from app.research_pipeline.quality import prepare_ticks

    clean, quality = prepare_ticks(ticks, cfg.symbol)
    if clean is None:
        return [{
            "timestamp": None,
            "instrument": cfg.symbol,
            "target": bundle.get("target"),
            "horizon_seconds": int(cfg.horizon_seconds),
            "barrier_distance": None,
            "barrier_unit": None,
            "calibrated_probability": None,
            "model_version": bundle.get("version"),
            "data_quality": quality.get("status", "incomplete"),
            "decision": "no_signal",
            "reason": "tick table could not be prepared",
            "order_placed": False,
        }]
    epochs = clean["epoch"].to_numpy(dtype=np.int64)
    times = decision_times(epochs, cfg, end_epoch=int(epochs[-1]))
    labels = label_entries(epochs, clean["quote"].to_numpy(dtype=float), times, cfg)
    by_time = {int(row.entry_epoch): row for row in labels.itertuples(index=False)}
    records = []
    for stamp in times:
        record = score_asof(clean, bundle, int(stamp), cfg=cfg)
        label = by_time.get(int(stamp))
        if label is not None and label.status == "ok":
            column = {"direction": "y_direction", "upper_touch": "y_upper", "lower_touch": "y_lower"}[bundle["target"]]
            value = getattr(label, column)
            record["realized_outcome_if_in_file"] = None if pd.isna(value) else int(value)
        else:
            record["realized_outcome_if_in_file"] = None
        records.append(record)
    return records


def append_jsonl(path: str | Path, record: dict) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def _combined_probability(bundle, frame, epochs, quotes, cfg: ExperimentConfig) -> float:
    from app.research_pipeline.modeling import design_matrix

    columns = list(bundle["columns"])
    values, _ = design_matrix(frame, columns, bundle["fills"])
    members = bundle.get("ensemble_members") or [bundle["primary_model"]]
    sequence = None
    if "lstm" in members:
        tables = build_tables(epochs, quotes, cfg)
        sequences, ok = sequence_matrix(tables, frame["entry_epoch"].to_numpy(), bundle["models"]["lstm"]["seq_len"])
        if not bool(ok[0]):
            raise RuntimeError("LSTM sequence is incomplete")
        sequence = sequences
    calibrated = []
    for name in members:
        model = bundle["models"][name]
        if name == "momentum":
            raw = predict_bundle(model, values, ret=frame["ret_1m"].to_numpy())
        elif name == "lstm":
            raw = predict_bundle(model, sequence)
        else:
            raw = predict_bundle(model, values)
        calibrated.append(apply_calibrator(bundle["calibrators"][name], raw))
    matrix = np.column_stack(calibrated)
    stacker = bundle.get("stacker")
    if stacker is not None and len(members) > 1:
        return float(stacker.predict_proba(matrix)[:, 1][0])
    if bundle.get("ensemble_members"):
        return float(matrix.mean(axis=1)[0])
    return float(matrix[0, 0])


def _prepare_arrays(ticks: pd.DataFrame, cfg: ExperimentConfig) -> tuple[np.ndarray, np.ndarray, str]:
    from app.research_pipeline.quality import prepare_ticks

    clean, _report = prepare_ticks(ticks, cfg.symbol)
    if clean is None or len(clean) < 3:
        return np.array([], dtype=np.int64), np.array([], dtype=float), "incomplete"
    return (
        clean["epoch"].to_numpy(dtype=np.int64),
        clean["quote"].to_numpy(dtype=float),
        "ok",
    )


def _quality_at(epochs: np.ndarray, asof: int, max_gap: int) -> str:
    if len(epochs) < 3:
        return "incomplete"
    idx = int(np.searchsorted(epochs, int(asof), side="right") - 1)
    if idx < 1:
        return "incomplete"
    if int(asof) - int(epochs[idx]) > int(max_gap):
        return "stale"
    if int(epochs[idx]) - int(epochs[idx - 1]) > int(max_gap):
        return "stale"
    return "ok"
