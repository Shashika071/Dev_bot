"""Append-only shadow decision log for calibration of accepted and rejected forecasts."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from app.config import settings


def shadow_log_path() -> str:
    return os.path.join(settings.model_dir, "shadow", "decisions.jsonl")


def append_shadow_decision(record: dict[str, Any]) -> None:
    path = shadow_log_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        payload = {"logged_at": datetime.now(timezone.utc).isoformat(), **record}
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, default=str) + "\n")
        if os.path.getsize(path) > 5_000_000:
            os.replace(path, path + ".1")
    except OSError:
        return
