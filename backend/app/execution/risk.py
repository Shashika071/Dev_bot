"""Recorded realized loss used by the optional execution gate."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from app.config import settings


def _path() -> str:
    return os.path.join(settings.model_dir, "execution_risk.json")


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def load_loss_today() -> float:
    path = _path()
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return 0.0
    if str(payload.get("day")) != _today():
        return 0.0
    try:
        return max(0.0, float(payload.get("realized_loss") or 0))
    except (TypeError, ValueError):
        return 0.0


def record_realized_loss(amount: float) -> float:
    loss = load_loss_today() + max(0.0, float(amount))
    os.makedirs(settings.model_dir, exist_ok=True)
    with open(_path(), "w", encoding="utf-8") as handle:
        json.dump({"day": _today(), "realized_loss": loss}, handle)
    return loss
