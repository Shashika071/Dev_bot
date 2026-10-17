"""Training device selection — prefer GPU when available and enabled."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@lru_cache(maxsize=1)
def train_gpu_enabled() -> bool:
    try:
        from app.config import settings

        return bool(getattr(settings, "train_use_gpu", True))
    except Exception:
        return True


@lru_cache(maxsize=1)
def torch_device() -> str:
    """Return 'cuda' or 'cpu' for PyTorch LSTM."""
    if not train_gpu_enabled():
        return "cpu"
    try:
        import torch

        if torch.cuda.is_available():
            name = torch.cuda.get_device_name(0)
            logger.info("train_device_torch", device="cuda", name=name)
            return "cuda"
    except Exception as exc:
        logger.info("train_device_torch_cpu", reason=str(exc))
    return "cpu"


@lru_cache(maxsize=1)
def catboost_task_type() -> str:
    """CatBoost 'GPU' or 'CPU'."""
    if not train_gpu_enabled():
        return "CPU"
    # CatBoost GPU needs a working CUDA runtime; probe lightly.
    try:
        from catboost.utils import get_gpu_device_count

        n = int(get_gpu_device_count())
        if n > 0:
            logger.info("train_device_catboost", device="GPU", count=n)
            return "GPU"
    except Exception as exc:
        logger.info("train_device_catboost_cpu", reason=str(exc))
    return "CPU"


@lru_cache(maxsize=1)
def xgboost_device_kwargs() -> dict[str, Any]:
    """XGBoost 2.x device kwargs, or empty for CPU."""
    if not train_gpu_enabled():
        return {}
    try:
        import torch

        # Prefer the same CUDA visibility torch sees; avoids half-broken drivers.
        if torch.cuda.is_available():
            logger.info("train_device_xgboost", device="cuda")
            return {"device": "cuda", "tree_method": "hist"}
    except Exception:
        pass
    try:
        # Fallback: try cuda without torch
        import subprocess

        out = subprocess.run(
            ["nvidia-smi", "-L"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if out.returncode == 0 and "GPU" in (out.stdout or ""):
            logger.info("train_device_xgboost", device="cuda", via="nvidia-smi")
            return {"device": "cuda", "tree_method": "hist"}
    except Exception as exc:
        logger.info("train_device_xgboost_cpu", reason=str(exc))
    return {}


def describe_train_devices() -> dict[str, str]:
    return {
        "gpu_setting": "on" if train_gpu_enabled() else "off",
        "torch": torch_device(),
        "catboost": catboost_task_type(),
        "xgboost": "cuda" if xgboost_device_kwargs() else "cpu",
    }
