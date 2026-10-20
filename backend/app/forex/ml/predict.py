"""Load the EURUSD hybrid filter and score a live feature row."""
from __future__ import annotations

from pathlib import Path

import joblib

from app.forex.ml.dataset import FEATURE_COLUMNS, feature_vector, features_from_live_frames
from app.forex.ml.train import artifact_paths, load_meta


def ml_status(model_base: str | Path) -> dict:
    """Report inactive / invalid / installed. Scoring readiness is per-request."""
    paths = artifact_paths(model_base)
    meta = load_meta(model_base)
    if not meta:
        return {
            "ready": False,
            "installed": False,
            "state": "inactive",
            "reason": "ML filter inactive — download history and train first",
            "threshold": None,
            "metrics": None,
        }
    if not paths["model"].exists():
        return {
            "ready": False,
            "installed": False,
            "state": "invalid",
            "reason": "Model metadata present but artifact missing",
            "threshold": meta.get("threshold"),
            "metrics": meta.get("metrics"),
        }
    if not meta.get("promoted", True):
        # Older artifacts without promoted flag still count as installed.
        if "promoted" in meta and meta.get("promoted") is False:
            return {
                "ready": False,
                "installed": False,
                "state": "candidate",
                "reason": "Candidate model not promoted — retrain or promote an accepted artifact",
                "threshold": meta.get("threshold"),
                "metrics": meta.get("metrics"),
            }
    return {
        "ready": True,
        "installed": True,
        "state": "ready",
        "reason": "EURUSD hybrid filter loaded",
        "threshold": meta.get("threshold", 0.55),
        "metrics": meta.get("metrics"),
        "trained_at": meta.get("trained_at"),
        "label_kind": meta.get("label_kind"),
    }


def predict_frames(model_base: str | Path, frames: dict, *, now: int, checklist_direction: str) -> dict:
    """Return hybrid ML view; does not authorize trades alone.

    States:
    - inactive: no model → hybrid_passed None (rules-only)
    - installed but features missing/invalid → hybrid_passed False (must block)
    - ready → agree/WAIT gate
    """
    base = ml_status(model_base)
    if base["state"] in {"inactive", "invalid", "candidate"}:
        return {
            **base,
            "direction": None,
            "probability": None,
            "agrees": None,
            "hybrid_passed": None,
        }
    paths = artifact_paths(model_base)
    try:
        bundle = joblib.load(paths["model"])
        pipe = bundle["pipeline"]
        features = bundle.get("features") or FEATURE_COLUMNS
        if list(features) != list(FEATURE_COLUMNS):
            return {
                **base,
                "ready": False,
                "installed": True,
                "state": "invalid",
                "reason": "Installed model feature list mismatch",
                "direction": None,
                "probability": None,
                "agrees": None,
                "hybrid_passed": False,
            }
        row = features_from_live_frames(frames, now=now)
        if row is None:
            return {
                **base,
                "ready": False,
                "installed": True,
                "state": "unavailable",
                "reason": "Installed ML filter cannot build closed-bar features",
                "direction": None,
                "probability": None,
                "agrees": None,
                "hybrid_passed": False,
            }
        vector = feature_vector(row).reshape(1, -1)
        proba_rise = float(pipe.predict_proba(vector)[0, 1])
    except Exception as exc:
        return {
            **base,
            "ready": False,
            "installed": True,
            "state": "unavailable",
            "reason": f"Installed ML filter failed ({type(exc).__name__})",
            "direction": None,
            "probability": None,
            "agrees": None,
            "hybrid_passed": False,
        }
    threshold = float(base["threshold"] or 0.55)
    if proba_rise >= threshold:
        direction = "RISE"
        probability = proba_rise
    elif proba_rise <= 1.0 - threshold:
        direction = "FALL"
        probability = 1.0 - proba_rise
    else:
        direction = "WAIT"
        probability = max(proba_rise, 1.0 - proba_rise)
    agrees = checklist_direction in {"RISE", "FALL"} and direction == checklist_direction
    hybrid_passed = bool(agrees and direction != "WAIT")
    return {
        **base,
        "ready": True,
        "installed": True,
        "state": "ready",
        "direction": direction,
        "probability": round(probability, 4),
        "p_rise": round(proba_rise, 4),
        "agrees": agrees,
        "hybrid_passed": hybrid_passed,
        "features_used": FEATURE_COLUMNS,
    }


def ml_blocks_entry(ml_view: dict) -> bool:
    """True when an installed filter must prevent emission."""
    if ml_view.get("installed") and ml_view.get("state") == "unavailable":
        return True
    if ml_view.get("installed") and ml_view.get("state") == "invalid":
        return True
    if ml_view.get("ready") and not ml_view.get("hybrid_passed"):
        return True
    return False
