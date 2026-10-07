"""Disk cache for trade-account snapshot (UI reads; live session writes)."""

from __future__ import annotations

import json
import os
import time
from typing import Any, Optional

from app.config import settings

ACCOUNT_CACHE_TTL_SECONDS = 120


def account_cache_path() -> str:
    return os.path.join(settings.model_dir, "deriv_trade_account.json")


def clear_account_cache() -> None:
    try:
        path = account_cache_path()
        if os.path.isfile(path):
            os.remove(path)
    except Exception:
        pass


def read_account_cache() -> Optional[dict[str, Any]]:
    path = account_cache_path()
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            cached = json.load(f)
        if not isinstance(cached, dict):
            return None
        fetched_at = float(cached.get("fetched_at") or cached.get("updated_at") or 0)
        age = max(0.0, time.time() - fetched_at) if fetched_at else 1e9
        return {
            **cached,
            "cached": True,
            "cache_age_seconds": int(age),
            "cache_ttl_seconds": ACCOUNT_CACHE_TTL_SECONDS,
            "refresh_allowed_in": max(0, int(ACCOUNT_CACHE_TTL_SECONDS - age)),
        }
    except Exception:
        return None


def write_account_cache(summary: dict[str, Any]) -> dict[str, Any]:
    now = time.time()
    out = {
        **summary,
        "fetched_at": float(summary.get("fetched_at") or now),
        "updated_at": float(summary.get("updated_at") or now),
        "cached": False,
        "cache_age_seconds": 0,
        "cache_ttl_seconds": ACCOUNT_CACHE_TTL_SECONDS,
        "refresh_allowed_in": ACCOUNT_CACHE_TTL_SECONDS,
    }
    try:
        os.makedirs(settings.model_dir, exist_ok=True)
        with open(account_cache_path(), "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, default=str)
    except Exception:
        pass
    return out
