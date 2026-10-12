"""Model activation checks.

Demonstrated-edge evidence and the 14-day shadow wait do not block
activation. Live quote, candle, confluence, and trend checks still apply.
"""

from __future__ import annotations

from datetime import datetime


def promotion_blockers(metadata: dict | None, *, now: datetime | None = None) -> list[str]:
    """Nothing here blocks turning a saved model on."""
    del metadata, now
    return []
