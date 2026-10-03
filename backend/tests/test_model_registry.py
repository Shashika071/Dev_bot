"""Model registry activation helpers (unit-level with mocks)."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.ml.registry import save_and_activate_model_version


@pytest.mark.asyncio
async def test_save_and_activate_model_version_sets_fields():
    session = AsyncMock()
    # deactivate execute + existing lookup
    session.execute = AsyncMock(
        side_effect=[
            MagicMock(),  # deactivate update
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),  # existing
        ]
    )
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.add = MagicMock()

    meta = {
        "version_tag": "v-upper-test",
        "direction": "upper",
        "symbol": "R_100",
        "selected_pipeline": "catboost",
        "barrier_distance": 0.1,
        "duration_seconds": 540,
        "has_demonstrated_edge": False,
        "feature_columns": ["return_10"],
        "metrics": {"auc_roc": 0.55, "brier_score": 0.2},
        "split_info": {"train": 10, "val": 5, "cal": 5, "test": 5},
        "paths": {"catboost": "/tmp/m.cbm", "calibrator": "/tmp/c.pkl"},
        "calibrator_path": "/tmp/c.pkl",
        "model_path": "/tmp/m.cbm",
    }
    row = await save_and_activate_model_version(session, meta, {})
    assert row.version_tag == "v-upper-test"
    assert row.is_active is True
    assert row.direction == "upper"
    session.add.assert_called()
    session.commit.assert_awaited()
