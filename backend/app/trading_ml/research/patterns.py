"""Compare the current row with earlier situations. Similarity is not a trade."""
from __future__ import annotations

import numpy as np
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import StandardScaler

from app.synthetic.ml import wilson_lower
from app.trading_ml.features.causal import FEATURE_COLUMNS


def structure_report(frame) -> list[dict]:
    """Describe earlier structure outcomes. A rate here cannot authorize a trade."""
    if frame is None or len(frame) == 0 or "label" not in getattr(frame, "columns", []):
        return []
    rows = frame.dropna(subset=["label"])
    if rows.empty:
        return []
    base = float(rows["label"].mean())
    specs = (
        ("Breakout up", "breakout_up"),
        ("Breakout down", "breakout_down"),
        ("Pullback", "pullback"),
    )
    report = []
    for name, column in specs:
        if column not in rows.columns:
            continue
        chosen = rows[rows[column] == 1]
        total = int(len(chosen))
        hits = int(chosen["label"].sum()) if total else 0
        report.append({
            "name": name,
            "samples": total,
            "rise_rate": None if not total else hits / total,
            "wilson_lower": wilson_lower(hits, total) if total else None,
            "base_rate": base,
            "supports_trade": False,
            "reason": "This is a historical rate on earlier candles. It does not authorize a signal.",
        })
    return report


def historical_neighbors(train_values: np.ndarray, train_labels: np.ndarray,
                         query: np.ndarray, *, candidate: str | None, payout: float | None) -> dict:
    labels = np.asarray(train_labels, dtype=int)
    values = np.asarray(train_values, dtype=float)
    current = np.asarray(query, dtype=float).reshape(1, -1)
    if len(labels) < 30 or not np.isfinite(values).all() or not np.isfinite(current).all():
        return {"available": False, "supports_candidate": False, "opposes_candidate": False,
                "reason": "Not enough earlier finite situations for a neighbor comparison."}
    scaler = StandardScaler().fit(values)
    neighbors = min(15, len(labels) // 5)
    if neighbors < 5:
        return {"available": False, "supports_candidate": False, "opposes_candidate": False,
                "reason": "The neighbor sample is too small to judge consistency."}
    model = NearestNeighbors(n_neighbors=neighbors).fit(scaler.transform(values))
    distances, indices = model.kneighbors(scaler.transform(current))
    chosen = labels[indices[0]]
    rise_rate = float(chosen.mean())
    fall_rate = 1 - rise_rate
    rise_lower = wilson_lower(int(chosen.sum()), len(chosen))
    fall_lower = wilson_lower(int((chosen == 0).sum()), len(chosen))
    supports = False
    opposes = False
    if candidate == "RISE" and rise_lower is not None and rise_lower > 0.55:
        supports = True
    if candidate == "FALL" and fall_lower is not None and fall_lower > 0.55:
        supports = True
    if candidate == "RISE" and fall_lower is not None and fall_lower > 0.55:
        opposes = True
    if candidate == "FALL" and rise_lower is not None and rise_lower > 0.55:
        opposes = True
    if payout is None:
        supports = False
    if opposes:
        reason = "Nearby historical outcomes lean against the candidate. Resemblance is not treated as an edge."
    elif supports:
        reason = "Nearby historical outcomes agree with the candidate, but this still does not authorize a trade by itself."
    else:
        reason = "Historical neighbors are inconclusive, so resemblance is not used as evidence."
    return {
        "available": True, "neighbors": neighbors, "rise_rate": rise_rate, "fall_rate": fall_rate,
        "median_distance": float(np.median(distances[0])), "supports_candidate": supports,
        "opposes_candidate": opposes, "reason": reason, "features": len(FEATURE_COLUMNS),
    }
