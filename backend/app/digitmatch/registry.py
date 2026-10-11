"""Persist a trained bundle. Nothing here marks a model active."""

from __future__ import annotations

import hashlib
import json
import os
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
    run_dir = directory / str(bundle["dataset_version"])
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / f"{bundle['selected_model']}.joblib"
    temporary = path.with_suffix(".joblib.tmp")
    joblib.dump(payload, temporary)
    digest = hashlib.sha256(temporary.read_bytes()).hexdigest()
    if path.exists():
        existing = hashlib.sha256(path.read_bytes()).hexdigest()
        temporary.unlink(missing_ok=True)
        if existing != digest:
            raise FileExistsError(
                f"Refusing to overwrite {path.name} for dataset {bundle['dataset_version']}"
            )
    else:
        os.replace(temporary, path)
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
    meta_path = path.with_suffix(".json")
    temporary_meta = meta_path.with_suffix(".json.tmp")
    temporary_meta.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    os.replace(temporary_meta, meta_path)
    return meta


def load_bundle(path: str, *, expected_schema: str = "dm-features-v1") -> dict:
    file = Path(path)
    raw = file.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    meta_path = file.with_suffix(".json")
    if not meta_path.exists():
        raise ValueError("model metadata missing")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("checksum") != digest:
        raise ValueError("model checksum mismatch")
    if expected_schema and meta.get("schema") != expected_schema:
        raise ValueError("model schema mismatch")
    payload = joblib.load(file)
    if payload.get("schema") != meta.get("schema"):
        raise ValueError("model schema mismatch")
    if payload.get("dataset_version") != meta.get("dataset_version"):
        raise ValueError("model dataset mismatch")
    return payload
