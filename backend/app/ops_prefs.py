"""
UI-owned runtime ops prefs.

.env values are first-boot defaults only. After deploy, change day-to-day
gates from the Setup UI — not by editing .env.prod on the VPS.

Secrets / infra (DB password, SECRET_KEY, URLs, ports) stay in .env forever.
"""

from __future__ import annotations

import json
import os
from typing import Any

import structlog

from app.config import settings

logger = structlog.get_logger(__name__)


def _prefs_path() -> str:
    return os.path.join(settings.model_dir, "ops_ui_prefs.json")


def _defaults() -> dict[str, Any]:
    return {
        "max_signals_per_day": int(settings.max_signals_per_day),
        "signal_cooldown_seconds": int(settings.signal_cooldown_seconds),
        "manual_min_confidence": float(settings.manual_min_confidence),
        "manual_min_margin_over_breakeven": float(settings.manual_min_margin_over_breakeven),
        "min_ev_margin": float(settings.min_ev_margin),
        "min_calibration_samples": int(settings.min_calibration_samples),
        "require_touch_confluence": bool(settings.require_touch_confluence),
        "confluence_min_score": float(settings.confluence_min_score),
        "confluence_min_gap": float(settings.confluence_min_gap),
        "auto_pause_enabled": bool(settings.auto_pause_enabled),
        "auto_pause_min_resolved": int(settings.auto_pause_min_resolved),
        "auto_pause_ci_margin": float(settings.auto_pause_ci_margin),
    }


_KEYS = set(_defaults().keys())


def _clamp(prefs: dict[str, Any]) -> dict[str, Any]:
    out = _defaults()
    out.update({k: prefs[k] for k in prefs if k in _KEYS})
    out["max_signals_per_day"] = max(0, min(10, int(out["max_signals_per_day"])))
    out["signal_cooldown_seconds"] = max(0, min(7200, int(out["signal_cooldown_seconds"])))
    out["manual_min_confidence"] = max(0.5, min(0.99, float(out["manual_min_confidence"])))
    out["manual_min_margin_over_breakeven"] = max(
        0.0, min(0.5, float(out["manual_min_margin_over_breakeven"]))
    )
    out["min_ev_margin"] = max(0.0, min(0.5, float(out["min_ev_margin"])))
    out["min_calibration_samples"] = max(5, min(200, int(out["min_calibration_samples"])))
    out["require_touch_confluence"] = bool(out["require_touch_confluence"])
    out["confluence_min_score"] = max(0.0, min(50.0, float(out["confluence_min_score"])))
    out["confluence_min_gap"] = max(0.0, min(20.0, float(out["confluence_min_gap"])))
    out["auto_pause_enabled"] = bool(out["auto_pause_enabled"])
    out["auto_pause_min_resolved"] = max(5, min(200, int(out["auto_pause_min_resolved"])))
    out["auto_pause_ci_margin"] = max(-0.1, min(0.1, float(out["auto_pause_ci_margin"])))
    return out


def load_ops_prefs() -> dict[str, Any]:
    base = _defaults()
    path = _prefs_path()
    try:
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                base.update({k: saved[k] for k in saved if k in _KEYS})
    except Exception as e:
        logger.warning("ops_prefs_load_failed", error=str(e))
    return _clamp(base)


def save_ops_prefs(prefs: dict[str, Any]) -> dict[str, Any]:
    merged = _clamp({**_defaults(), **(prefs or {})})
    os.makedirs(settings.model_dir, exist_ok=True)
    path = _prefs_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2)
    logger.info("ops_prefs_saved", path=path, keys=list(merged.keys()))
    return merged


def ops(key: str):
    """Read one UI-overridable ops value (falls back to .env default)."""
    return load_ops_prefs()[key]
