"""Stale-data suppression helpers and worker endpoint auth."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.api.signals import require_internal_secret
from app.config import settings
from app.signal_engine.generator import SignalGenerator
from app.signal_engine.ev_filter import EVFilter
from app.signal_engine.daily_cap import DailyCapManager
from app.signal_engine.lifecycle import SignalLifecycleManager
from app.strategies.registry import StrategyRegistry


def test_require_internal_secret_rejects_missing():
    with patch.object(settings, "internal_api_secret", "expected-secret"):
        with pytest.raises(HTTPException) as exc:
            require_internal_secret(x_internal_secret=None)
        assert exc.value.status_code == 403


def test_require_internal_secret_rejects_wrong():
    with patch.object(settings, "internal_api_secret", "expected-secret"):
        with pytest.raises(HTTPException) as exc:
            require_internal_secret(x_internal_secret="wrong")
        assert exc.value.status_code == 403


def test_require_internal_secret_accepts():
    with patch.object(settings, "internal_api_secret", "expected-secret"):
        assert require_internal_secret(x_internal_secret="expected-secret") is None


@pytest.mark.asyncio
async def test_stale_ticks_suppress_signal():
    strategies = StrategyRegistry()
    strategies.register_defaults()
    gen = SignalGenerator(
        strategies,
        EVFilter(),
        DailyCapManager(),
        SignalLifecycleManager(),
    )
    # Fake model so prerequisites pass past models check — still need settings
    gen.set_model(
        model=MagicModel(),
        calibrator=None,
        version_id=1,
        has_edge=True,
        direction="upper",
    )

    session = AsyncMock()
    with patch(
        "app.signal_engine.generator.get_latest_confirmed_settings",
        new=AsyncMock(return_value=MagicSettings()),
    ):
        import pandas as pd

        result = await gen.evaluate_and_generate(
            session=session,
            features_df=pd.DataFrame({"return_10": [0.01]}),
            current_price=100.0,
            barrier_distance=0.09,
            symbol="R_100",
            last_tick_age_seconds=999.0,  # stale
        )
    assert result is None


class MagicModel:
    def predict_proba(self, X):
        import numpy as np
        return np.array([0.99])


class MagicSettings:
    barrier_direction = "upper"
    barrier_input = "0.09"
    barrier_unit_description = "relative"
    duration_seconds = 540
