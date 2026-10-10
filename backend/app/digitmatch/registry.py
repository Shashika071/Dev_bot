"""Persist a trained bundle. Nothing here marks a model active."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import joblib

from app.config import settings


def model_root() -> Path:
    configured = Path(settings.model_dir) / "digitmatch"
    try:
        configured.mkdir(parents=True, exist_ok=True)
        return configured
    except OSError:
        fallback = Path(__file__).resolve().parents[2] / "data" / "digitmatch_models"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def save_bundle(bundle: dict, directory: Path | None = None) -> dict:
    directory = directory or model_root()
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "selected_model": bundle["selected_model"],
        "models": bundle["models"],
        "selector": bundle["selector"],
        "ensemble": bundle["ensemble"],
        "schema": bundle["schema"],
        "margin": bundle["margin"],
        "seed": bundle["seed"],
        "dataset_version": bundle["dataset_version"],
    }
    path = directory / f"{bundle['dataset_version']}-{bundle['selected_model']}.joblib"
    joblib.dump(payload, path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    meta = {
        "path": str(path),
        "checksum": digest,
        "selected_model": bundle["selected_model"],
        "schema": bundle["schema"],
        "seed": bundle["seed"],
        "dataset_version": bundle["dataset_version"],
        "calibration_method": bundle["calibration_method"],
        "dependency_versions": bundle["dependency_versions"],
        "margin": bundle["margin"],
        "margin_source": bundle["margin_source"],
        "split_counts": bundle["split_counts"],
        "selection_log_loss": bundle["selection_log_loss"],
        "final_test": bundle["final_test"],
        "comparison_to_uniform": bundle["comparison_to_uniform"],
        "has_demonstrated_edge": False,
        "edge_statement": bundle["edge_statement"],
        "walk_forward": bundle["walk_forward"],
        "failures": bundle["failures"],
        "target_note": bundle["target_note"],
        "time_start": bundle["time_start"],
        "time_end": bundle["time_end"],
        "assumed_payout_simulation": bundle["assumed_payout_simulation"],
        "promoted": False,
    }
    path.with_suffix(".json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def load_bundle(path: str) -> dict:
    return joblib.load(path)
