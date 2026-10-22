"""Forecast the next realized-volatility window from past windows only."""
from __future__ import annotations

import math

import numpy as np
from sklearn.linear_model import Ridge


def forecast_volatility(frame, train_epochs: set[int], live_epoch: int) -> dict:
    work = frame.dropna(subset=["rv_10", "rv_20", "rv_60"]).copy()
    work["target"] = work["rv_20"].shift(-1)
    work["next_epoch"] = work["epoch"].shift(-1)
    fit = work[work["epoch"].isin(train_epochs) & work["next_epoch"].isin(train_epochs) & work["target"].notna()]
    live = work[work["epoch"] == live_epoch]
    if len(fit) < 40 or live.empty:
        return {"available": False, "text": "unavailable", "forecast": None, "recent": None,
                "state": "unavailable", "reason": "Not enough past volatility windows to fit a forecast."}
    model = Ridge(alpha=1.0)
    columns = ["rv_10", "rv_20", "rv_60"]
    model.fit(fit[columns].to_numpy(dtype=float), fit["target"].to_numpy(dtype=float))
    forecast = float(model.predict(live[columns].to_numpy(dtype=float))[0])
    recent = float(live["rv_20"].iloc[0])
    if not math.isfinite(forecast) or not math.isfinite(recent):
        return {"available": False, "text": "unavailable", "forecast": None, "recent": recent,
                "state": "unavailable", "reason": "The volatility forecast was not finite."}
    if forecast > recent * 1.05:
        state = "expansion"
    elif forecast < recent * 0.95:
        state = "contraction"
    else:
        state = "stable"
    return {
        "available": True, "forecast": forecast, "recent": recent, "state": state,
        "text": f"rv20 forecast {forecast:.6f} versus recent {recent:.6f} ({state})",
        "reason": "Ridge forecast of the next 20-bar realized volatility, fit on earlier rows.",
    }


def train_epochs_before(frame, cutoff_epoch: int) -> set[int]:
    epochs = frame.loc[frame["epoch"] < cutoff_epoch, "epoch"]
    return set(int(epoch) for epoch in epochs.to_numpy())
