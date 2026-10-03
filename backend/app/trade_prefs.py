"""
Opt-in auto-trade preferences + encrypted Deriv API token.

Token is stored separately under MODEL_DIR (never returned in full to the UI).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from typing import Any, Optional

import structlog
from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

logger = structlog.get_logger(__name__)


def _prefs_path() -> str:
    return os.path.join(settings.model_dir, "trade_ui_prefs.json")


def _token_path() -> str:
    return os.path.join(settings.model_dir, "deriv_trade_token.enc")


def _fernet() -> Fernet:
    # Derive a stable 32-byte Fernet key from SECRET_KEY
    digest = hashlib.sha256(settings.secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _defaults() -> dict[str, Any]:
    return {
        "auto_trade_enabled": False,
        "trade_stake": 1.0,
        "force_min_probability": 0.80,
        "trade_currency": "USD",
        # PAT apps use alphanumeric App ID from developers.deriv.com
        "deriv_app_id": str(settings.deriv_app_id or ""),
    }


_KEYS = set(_defaults().keys())


def _clamp(prefs: dict[str, Any]) -> dict[str, Any]:
    out = _defaults()
    out.update({k: prefs[k] for k in prefs if k in _KEYS})
    out["auto_trade_enabled"] = bool(out["auto_trade_enabled"])
    out["trade_stake"] = max(0.35, min(10000.0, float(out["trade_stake"])))
    out["force_min_probability"] = max(0.50, min(0.99, float(out["force_min_probability"])))
    cur = str(out.get("trade_currency") or "USD").strip().upper() or "USD"
    out["trade_currency"] = cur[:8]
    aid = str(out.get("deriv_app_id") or settings.deriv_app_id or "").strip()
    out["deriv_app_id"] = aid[:64]
    return out


def resolve_deriv_app_id() -> str:
    """UI trade prefs first, then .env DERIV_APP_ID."""
    prefs = load_trade_prefs()
    return str(prefs.get("deriv_app_id") or settings.deriv_app_id or "").strip()


def load_trade_prefs() -> dict[str, Any]:
    base = _defaults()
    path = _prefs_path()
    try:
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                base.update({k: saved[k] for k in saved if k in _KEYS})
    except Exception as e:
        logger.warning("trade_prefs_load_failed", error=str(e))
    return _clamp(base)


def save_trade_prefs(prefs: dict[str, Any]) -> dict[str, Any]:
    merged = _clamp({**_defaults(), **(prefs or {})})
    os.makedirs(settings.model_dir, exist_ok=True)
    path = _prefs_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2)
    logger.info("trade_prefs_saved", path=path, auto_trade=merged["auto_trade_enabled"])
    return merged


def save_trade_token(token: str) -> dict[str, Any]:
    token = str(token or "").strip()
    if not token or len(token) < 8:
        raise ValueError("API token looks too short")
    os.makedirs(settings.model_dir, exist_ok=True)
    blob = _fernet().encrypt(token.encode("utf-8"))
    with open(_token_path(), "wb") as f:
        f.write(blob)
    # Mask only — never persist plaintext alongside
    mask = f"...{token[-4:]}"
    meta = {"token_configured": True, "token_mask": mask}
    meta_path = os.path.join(settings.model_dir, "deriv_trade_token_meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f)
    logger.info("trade_token_saved", mask=mask)
    return meta


def clear_trade_token() -> None:
    for p in (_token_path(), os.path.join(settings.model_dir, "deriv_trade_token_meta.json")):
        try:
            if os.path.isfile(p):
                os.remove(p)
        except Exception as e:
            logger.warning("trade_token_clear_failed", path=p, error=str(e))
    logger.info("trade_token_cleared")


def get_trade_token() -> Optional[str]:
    path = _token_path()
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as f:
            blob = f.read()
        return _fernet().decrypt(blob).decode("utf-8")
    except InvalidToken:
        logger.error("trade_token_decrypt_failed")
        return None
    except Exception as e:
        logger.error("trade_token_read_failed", error=str(e))
        return None


def token_status() -> dict[str, Any]:
    meta_path = os.path.join(settings.model_dir, "deriv_trade_token_meta.json")
    configured = os.path.isfile(_token_path())
    mask = None
    if configured and os.path.isfile(meta_path):
        try:
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)
            mask = meta.get("token_mask")
        except Exception:
            pass
    if configured and not mask:
        tok = get_trade_token()
        mask = f"...{tok[-4:]}" if tok else None
    return {
        "token_configured": configured,
        "token_mask": mask,
    }


def trade_prefs_public() -> dict[str, Any]:
    prefs = load_trade_prefs()
    return {**prefs, **token_status()}
