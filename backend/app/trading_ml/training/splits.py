"""Walk-forward splits with an extra embargo before every later window."""
from __future__ import annotations

import pandas as pd

from app.synthetic.ml import purged_walk_forward


def embargo_splits(frame: pd.DataFrame, embargo_seconds: int, *, folds: int = 3,
                   test_fraction: float = 0.2):
    splits, development, test = purged_walk_forward(frame, folds=folds, test_fraction=test_fraction)
    embargoed = []
    for train, validation in splits:
        start = int(validation["decision_epoch"].min())
        kept = train[train["decision_epoch"] < start - embargo_seconds].copy()
        if kept.empty:
            raise ValueError("Embargo removed every training row. More history is required.")
        embargoed.append((kept, validation))
    test_start = int(test["decision_epoch"].min())
    development = development[development["decision_epoch"] < test_start - embargo_seconds].copy()
    if development.empty:
        raise ValueError("Embargo removed the development window. More history is required.")
    return embargoed, development, test
