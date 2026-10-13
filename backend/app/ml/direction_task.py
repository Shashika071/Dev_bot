"""Directional 5/15/30-minute labels and a leakage-safe training head.

Touch probability remains a separate task. These probabilities are not a
technical-analysis score.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from sklearn.dummy import DummyClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss
from sklearn.preprocessing import StandardScaler

from app.config import settings
from app.features.indicators import rsi
from app.signal_engine.chart_candles import aggregate_ohlc

HORIZONS = {
    "direction_5m": 300,
    "direction_15m": 900,
    "direction_30m": 1800,
}
CLASSES = ("down", "neutral", "up")


def direction_label(entry_price: float, future_price: float, atr: float, band_mult: float = 0.25) -> str:
    band = max(float(atr) * float(band_mult), abs(float(entry_price)) * 1e-6)
    change = float(future_price) - float(entry_price)
    if change > band:
        return "up"
    if change < -band:
        return "down"
    return "neutral"


def _atr_at(quotes: np.ndarray, end: int, window: int = 30) -> float:
    start = max(1, end - window)
    if end - start < 2:
        return abs(float(quotes[end]) * 0.0005)
    changes = np.abs(np.diff(quotes[start:end + 1]))
    value = float(np.mean(changes)) if len(changes) else 0.0
    return max(value, abs(float(quotes[end])) * 1e-6)


def _future_price(epochs: np.ndarray, quotes: np.ndarray, start: int, target_epoch: int) -> float | None:
    future = np.flatnonzero(epochs >= target_epoch)
    future = future[future > start]
    if len(future) == 0:
        return None
    return float(quotes[int(future[0])])


def closed_candle_features(candles: list[dict], decision_epoch: int, step: int = 60) -> dict[str, float]:
    """Features from candles that closed at or before the decision."""
    closed = [row for row in candles if int(row["epoch"]) + step <= int(decision_epoch)]
    if len(closed) < 15:
        return {}
    closes = pd.Series([float(row["close"]) for row in closed])
    last = closed[-1]
    prev = closed[-2]
    body = float(last["close"]) - float(last["open"])
    span = max(float(last["high"]) - float(last["low"]), 1e-9)
    rsi_value = float(rsi(closes, 14).iloc[-1])
    fast = float(closes.ewm(span=8, min_periods=8, adjust=False).mean().iloc[-1])
    slow = float(closes.ewm(span=21, min_periods=21, adjust=False).mean().iloc[-1]) if len(closes) >= 21 else fast
    return {
        "c1_body_ratio": body / span,
        "c1_return": (float(last["close"]) - float(prev["close"])) / max(abs(float(prev["close"])), 1e-9),
        "c1_rsi": 50.0 if rsi_value != rsi_value else rsi_value,
        "c1_ema_spread": (fast - slow) / max(abs(slow), 1e-9),
    }


def build_direction_rows(
    ticks: pd.DataFrame,
    horizon_seconds: int,
    *,
    spacing_seconds: int | None = None,
) -> pd.DataFrame:
    frame = ticks.dropna(subset=["epoch", "quote"]).sort_values("epoch").reset_index(drop=True)
    if len(frame) < 80:
        return pd.DataFrame()
    epochs = frame["epoch"].to_numpy(dtype=np.int64)
    quotes = frame["quote"].to_numpy(dtype=float)
    candles = aggregate_ohlc(((int(e), float(q)) for e, q in zip(epochs, quotes)), "1m", max_candles=0)
    spacing = int(spacing_seconds or horizon_seconds)
    rows = []
    start = 40
    while start < len(frame) - 2:
        entry_epoch = int(epochs[start])
        future = _future_price(epochs, quotes, start, entry_epoch + int(horizon_seconds))
        if future is None:
            break
        atr = _atr_at(quotes, start)
        past = quotes[max(0, start - 30):start + 1]
        returns = np.diff(past) / np.maximum(np.abs(past[:-1]), 1e-9)
        candle_features = closed_candle_features(candles, entry_epoch, 60)
        if not candle_features:
            start += max(1, spacing // 2)
            continue
        row = {
            "entry_epoch": entry_epoch,
            "label": direction_label(float(quotes[start]), future, atr),
            "ret_5": float(quotes[start] / quotes[max(0, start - 5)] - 1),
            "ret_15": float(quotes[start] / quotes[max(0, start - 15)] - 1),
            "vol_30": float(np.std(returns)) if len(returns) else 0.0,
            "atr": atr,
            **candle_features,
        }
        rows.append(row)
        start += max(1, int(np.searchsorted(epochs, entry_epoch + spacing) - start) or 1)
    return pd.DataFrame(rows)


def _split(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    n = len(frame)
    train_end = int(n * 0.7)
    cal_end = int(n * 0.85)
    return frame.iloc[:train_end], frame.iloc[train_end:cal_end], frame.iloc[cal_end:]


def features_now(ticks: pd.DataFrame) -> dict | None:
    """Feature row at the latest tick. No future price is read."""
    frame = ticks.dropna(subset=["epoch", "quote"]).sort_values("epoch").reset_index(drop=True)
    if len(frame) < 40:
        return None
    epochs = frame["epoch"].to_numpy(dtype=np.int64)
    quotes = frame["quote"].to_numpy(dtype=float)
    start = len(frame) - 1
    candles = aggregate_ohlc(((int(e), float(q)) for e, q in zip(epochs, quotes)), "1m", max_candles=0)
    candle_features = closed_candle_features(candles, int(epochs[start]), 60)
    if not candle_features:
        return None
    past = quotes[max(0, start - 30):start + 1]
    returns = np.diff(past) / np.maximum(np.abs(past[:-1]), 1e-9)
    return {
        "entry_epoch": int(epochs[start]),
        "ret_5": float(quotes[start] / quotes[max(0, start - 5)] - 1),
        "ret_15": float(quotes[start] / quotes[max(0, start - 15)] - 1),
        "vol_30": float(np.std(returns)) if len(returns) else 0.0,
        "atr": _atr_at(quotes, start),
        **candle_features,
    }


def train_direction_task(
    ticks: pd.DataFrame,
    task_id: str,
    *,
    symbol: str = "R_100",
    min_samples: int = 80,
    spacing_seconds: int | None = None,
) -> dict:
    if task_id not in HORIZONS:
        raise ValueError(f"Unknown direction task {task_id}")
    rows = build_direction_rows(ticks, HORIZONS[task_id], spacing_seconds=spacing_seconds)
    if len(rows) < min_samples:
        return {"error": f"Not enough direction samples: {len(rows)} < {min_samples}"}
    feature_cols = [col for col in rows.columns if col not in {"entry_epoch", "label"}]
    train, cal, test = _split(rows)
    if train["label"].nunique() < 2 or len(test) < 5 or len(cal) < 5:
        return {"error": "Direction split does not contain enough classes or rows"}

    scaler = StandardScaler()
    x_train = scaler.fit_transform(train[feature_cols])
    x_cal = scaler.transform(cal[feature_cols])
    x_test = scaler.transform(test[feature_cols])
    model = LogisticRegression(max_iter=400)
    model.fit(x_train, train["label"])
    baseline = DummyClassifier(strategy="most_frequent")
    baseline.fit(x_train, train["label"])

    def safe_loss(estimator, features, labels) -> float:
        probabilities = estimator.predict_proba(features)
        classes = list(estimator.classes_)
        ordered = np.zeros((len(features), len(CLASSES)))
        for index, name in enumerate(classes):
            if name in CLASSES:
                ordered[:, CLASSES.index(name)] = probabilities[:, index]
        ordered = np.clip(ordered, 1e-6, 1)
        ordered = ordered / ordered.sum(axis=1, keepdims=True)
        return float(log_loss(labels, ordered, labels=list(CLASSES)))

    model_loss = safe_loss(model, x_test, test["label"])
    baseline_loss = safe_loss(baseline, x_test, test["label"])
    present = set(cal["label"].astype(str))
    checks = {
        "min_samples": len(rows) >= min_samples,
        "calibration": present == set(CLASSES) or len(present) >= 2 and len(cal) >= 5,
        "stability": len(train) >= 20 and train["label"].nunique() >= 2,
        "beats_baseline": model_loss < baseline_loss,
    }
    # A three-way forecast needs every class in calibration before it can be promoted.
    checks["calibration"] = present == set(CLASSES) and len(cal) >= 5
    ready = all(checks.values())
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    version_tag = f"v-{task_id}-{timestamp}"
    model_dir = settings.model_dir
    os.makedirs(model_dir, exist_ok=True)
    model_path = os.path.join(model_dir, f"{task_id}_{timestamp}.pkl")
    import joblib

    joblib.dump({"model": model, "scaler": scaler, "columns": feature_cols, "classes": list(model.classes_)}, model_path)
    metadata = {
        "version_tag": version_tag,
        "task_id": task_id,
        "symbol": symbol,
        "direction": task_id,
        "selected_pipeline": "logistic_regression",
        "model_path": model_path,
        "feature_columns": feature_cols,
        "promotion_checks": checks,
        "promotion_ready": ready,
        "has_demonstrated_edge": ready,
        "metrics": {"log_loss": model_loss, "baseline_log_loss": baseline_loss},
        "split_info": {"train": len(train), "cal": len(cal), "test": len(test)},
        "created_at": datetime.now(timezone.utc).isoformat(),
        "horizon_seconds": HORIZONS[task_id],
        "label_rule": "future return versus ATR band known at entry; neutral inside the band",
    }
    meta_path = os.path.join(model_dir, f"meta_{task_id}_{timestamp}.json")
    with open(meta_path, "w", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2)
    return {"metadata": metadata, "promotion_ready": ready, "selected_pipeline": "logistic_regression"}


def predict_direction(ticks: pd.DataFrame, metadata: dict) -> dict | None:
    import joblib

    path = metadata.get("model_path")
    if not path or not os.path.isfile(path) or ticks.empty:
        return None
    bundle = joblib.load(path)
    last = features_now(ticks)
    if last is None:
        return None
    row = pd.DataFrame([last])
    features = bundle["scaler"].transform(row[bundle["columns"]])
    probabilities = bundle["model"].predict_proba(features)[0]
    mapped = {name: 0.0 for name in CLASSES}
    for name, probability in zip(bundle["classes"], probabilities):
        if name in mapped:
            mapped[name] = float(probability)
    return {
        "available": True,
        "up": mapped["up"],
        "down": mapped["down"],
        "neutral": mapped["neutral"],
        "note": "Separate from the technical-analysis score. Active only after promotion.",
        "as_of_epoch": int(last["entry_epoch"]),
    }
