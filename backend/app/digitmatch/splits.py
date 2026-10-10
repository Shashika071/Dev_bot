"""Chronological partitions with purge of labels that cross a boundary."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class SplitFractions:
    train: float = 0.50
    calibration: float = 0.15
    selection: float = 0.15
    test: float = 0.20

    def validate(self) -> None:
        total = self.train + self.calibration + self.selection + self.test
        if abs(total - 1.0) > 1e-9:
            raise ValueError("split fractions must sum to 1")
        if min(self.train, self.calibration, self.selection, self.test) <= 0:
            raise ValueError("every split fraction must be positive")


def chronological_split(
    indexes: np.ndarray,
    *,
    fractions: SplitFractions | None = None,
    horizon: int = 5,
) -> dict[str, np.ndarray]:
    """Split example positions in time order. Do not shuffle.

    An example is purged when its target tick (index + horizon) lands in a
    later partition's decision-tick span.
    """
    fractions = fractions or SplitFractions()
    fractions.validate()
    indexes = np.asarray(indexes, dtype=int)
    order = np.argsort(indexes, kind="mergesort")
    indexes = indexes[order]
    n = len(indexes)
    if n < 4:
        raise ValueError("need at least four examples to allocate four partitions")
    c1 = int(n * fractions.train)
    c2 = int(n * (fractions.train + fractions.calibration))
    c3 = int(n * (fractions.train + fractions.calibration + fractions.selection))
    if not (0 < c1 < c2 < c3 < n):
        raise ValueError("not enough examples for four non-empty chronological partitions")
    bounds = {
        "train": (0, c1),
        "calibration": (c1, c2),
        "selection": (c2, c3),
        "test": (c3, n),
    }
    spans = {name: (int(indexes[a]), int(indexes[b - 1])) for name, (a, b) in bounds.items() if b > a}
    order_names = ["train", "calibration", "selection", "test"]
    kept: dict[str, list[int]] = {name: [] for name in order_names}
    purged: list[int] = []
    for name_i, name in enumerate(order_names):
        start, stop = bounds[name]
        later = order_names[name_i + 1 :]
        for pos in range(start, stop):
            decision = int(indexes[pos])
            target = decision + horizon
            crosses = False
            for later_name in later:
                later_start, _later_end = spans[later_name]
                if target >= later_start:
                    crosses = True
                    break
            if crosses:
                purged.append(decision)
            else:
                kept[name].append(decision)
    return {
        "order": order,
        "train": np.asarray(kept["train"], dtype=int),
        "calibration": np.asarray(kept["calibration"], dtype=int),
        "selection": np.asarray(kept["selection"], dtype=int),
        "test": np.asarray(kept["test"], dtype=int),
        "purged": np.asarray(purged, dtype=int),
    }


def rows_for(index: np.ndarray, wanted: np.ndarray) -> np.ndarray:
    wanted_set = set(int(v) for v in wanted)
    return np.asarray([i for i, value in enumerate(index) if int(value) in wanted_set], dtype=int)
