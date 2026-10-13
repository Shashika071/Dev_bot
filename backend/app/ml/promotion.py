"""Model activation checks.

A saved model stays inactive until its recorded sample, calibration,
stability, and baseline gates pass and a person promotes it.
"""

from __future__ import annotations

from datetime import datetime

_REQUIRED_GATES = ("min_samples", "calibration", "stability", "beats_baseline")


def promotion_blockers(metadata: dict | None, *, now: datetime | None = None) -> list[str]:
    """Reasons a saved model must not become the active model."""
    del now
    if not metadata:
        return ["missing training metadata"]
    reasons: list[str] = []
    checks = metadata.get("promotion_checks") or {}
    if not metadata.get("promotion_ready"):
        reasons.append("model has not passed promotion gates")
    for gate in _REQUIRED_GATES:
        if not checks.get(gate):
            reasons.append(f"missing gate: {gate}")
    return reasons
