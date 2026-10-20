"""Load the EURUSD hybrid filter and score a live feature row."""
from __future__ import annotations

import json
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


# Display call only. Near 50/50 stays NO TRADE. This does not authorize a buy.
IDEA_EDGE = 0.55
_IDEA_CACHE: dict[str, tuple[float, dict]] = {}


def _idea_sources(model_base: str | Path) -> list[tuple[Path, dict]]:
    """Active promoted model first, then newest unpromoted candidates."""
    paths = artifact_paths(model_base)
    found: list[tuple[Path, dict]] = []
    meta = load_meta(model_base)
    if meta and paths["model"].exists() and meta.get("promoted", True) is not False:
        found.append((paths["model"], meta))
    folder = paths["candidates"]
    if folder.exists():
        for candidate in sorted((path for path in folder.iterdir() if path.is_dir()), reverse=True):
            meta_path = candidate / "meta.json"
            model_path = candidate / "model.joblib"
            if not meta_path.exists() or not model_path.exists():
                continue
            try:
                candidate_meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            found.append((model_path, candidate_meta))
    return found


def _load_idea_bundle(model_path: Path) -> dict:
    stamp = model_path.stat().st_mtime
    cached = _IDEA_CACHE.get(str(model_path))
    if cached and cached[0] == stamp:
        return cached[1]
    bundle = joblib.load(model_path)
    _IDEA_CACHE[str(model_path)] = (stamp, bundle)
    return bundle


def score_idea(model_base: str | Path, frames: dict, *, now: int) -> dict:
    """Score a live Rise/Fall lean for display. Never installs the trade filter."""
    blank = {
        "role": "idea",
        "trades": False,
        "installed": False,
        "state": "unavailable",
        "direction": "NO TRADE",
        "p_rise": None,
        "p_fall": None,
        "confidence": None,
        "level": None,
        "threshold": IDEA_EDGE,
        "reason": "No EURUSD candidate to score",
        "held_out_accuracy": None,
        "held_out_auc": None,
    }
    for model_path, meta in _idea_sources(model_base):
        features = list(meta.get("features") or [])
        if features != list(FEATURE_COLUMNS):
            continue
        try:
            bundle = _load_idea_bundle(model_path)
            pipe = bundle["pipeline"]
            bundle_features = list(bundle.get("features") or features)
            if bundle_features != list(FEATURE_COLUMNS):
                continue
            row = features_from_live_frames(frames, now=now)
            if row is None:
                blank["reason"] = "Need closed 15m, 1h, and 4h bars before a confidence read"
                return blank
            proba_rise = float(pipe.predict_proba(feature_vector(row).reshape(1, -1))[0, 1])
        except Exception:
            continue
        if not 0.0 <= proba_rise <= 1.0:
            continue
        p_fall = 1.0 - proba_rise
        confidence = max(proba_rise, p_fall)
        if proba_rise >= IDEA_EDGE:
            direction = "RISE"
        elif proba_rise <= 1.0 - IDEA_EDGE:
            direction = "FALL"
        else:
            direction = "NO TRADE"
        if confidence < IDEA_EDGE:
            level = "low"
        elif confidence < 0.62:
            level = "modest"
        else:
            level = "stronger"
        metrics = meta.get("metrics") or {}
        accuracy = metrics.get("selected_accuracy")
        return {
            **blank,
            "state": "idea",
            "direction": direction,
            "p_rise": round(proba_rise, 4),
            "p_fall": round(p_fall, 4),
            "confidence": round(confidence, 4),
            "level": level,
            "reason": "Display-only lean from the latest compatible EURUSD model. Checklist still decides trades.",
            "held_out_accuracy": None if accuracy is None else round(float(accuracy), 4),
            "held_out_auc": None if metrics.get("test_auc") is None else round(float(metrics["test_auc"]), 4),
            "model_promoted": bool(meta.get("promoted")),
        }
    return blank


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
