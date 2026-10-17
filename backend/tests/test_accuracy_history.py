"""Bounded model-history loading and compatible live tick processing."""

import asyncio
from collections import deque
from types import SimpleNamespace

import numpy as np
import pytest

from app.accuracy.service import AccuracyService


class _HistoryClient:
    def __init__(self, service=None, repeat=False):
        self.epochs = np.arange(0, 60000, 2)
        self.requests = []
        self.service = service
        self.repeat = repeat

    async def get_ticks_history(self, symbol, *, count, end):
        self.requests.append((symbol, count, end))
        # Emulate the public endpoint's smaller page despite requesting 5,000.
        stop = len(self.epochs) if end == "latest" or self.repeat else int(np.searchsorted(self.epochs, end, side="right"))
        page = self.epochs[max(0, stop - 1000):stop]
        if self.service is not None and len(self.requests) == 1:
            # A tick arriving while older pages load must survive the merge.
            await self.service.on_tick({"tick": {"epoch": 60000, "quote": 123.45, "symbol": "R_100"}})
        return {"history": {"times": page.tolist(), "prices": [100.] * len(page)}}


def test_pages_past_server_cap_and_preserves_ticks_arriving_during_load(tmp_path):
    service = AccuracyService(tmp_path)
    service.history_seconds = 43200
    service.ticks = deque(maxlen=50400)
    service.loading_history = True
    service.client = _HistoryClient(service)
    asyncio.run(service._load_history())
    assert len(service.client.requests) > 1
    assert all(request[:2] == ("R_100", 5000) for request in service.client.requests)
    assert service.client.requests[0][2] == "latest"
    # Each prior page begins 2,000 seconds before the previous end.
    assert service.client.requests[1][2] == 57999
    assert service.ticks[-1] == (60000, 123.45)
    epochs = [tick[0] for tick in service.ticks]
    assert epochs == sorted(set(epochs))
    assert epochs[-1] - epochs[0] >= 43200 + 3600
    assert service.last_tick == {"epoch": 60000, "price": 123.45}


def test_compact_profile_requires_only_its_short_history(tmp_path):
    service = AccuracyService(tmp_path)
    service.client = _HistoryClient()
    asyncio.run(service._load_history())
    assert len(service.client.requests) == 1
    assert service.ticks.maxlen == 4000


def test_history_pagination_rejects_an_endpoint_that_does_not_advance(tmp_path):
    service = AccuracyService(tmp_path)
    service.history_seconds = 43200
    service.client = _HistoryClient(repeat=True)
    with pytest.raises(ValueError, match="did not advance"):
        asyncio.run(service._load_history())
    assert len(service.client.requests) == 2


def test_history_pagination_has_a_hard_page_limit(tmp_path, monkeypatch):
    monkeypatch.setattr("app.accuracy.service.MAX_HISTORY_PAGES", 2)
    service = AccuracyService(tmp_path)
    service.history_seconds = 43200
    service.client = _HistoryClient()
    with pytest.raises(ValueError, match="page limit"):
        asyncio.run(service._load_history())
    assert len(service.client.requests) == 2


def test_history_page_timeout_is_explicit_and_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr("app.accuracy.service.HISTORY_PAGE_TIMEOUT_SECONDS", .001)

    async def stalled(*args, **kwargs):
        await asyncio.sleep(3600)

    service = AccuracyService(tmp_path)
    service.client = SimpleNamespace(get_ticks_history=stalled)
    with pytest.raises(ValueError, match="Timed out fetching"):
        asyncio.run(service._load_history())


@pytest.mark.parametrize("history, expected_capacity", [(None, 4000), (43200, 50400)])
def test_start_uses_profile_lookback_without_changing_legacy_artifacts(
    tmp_path, monkeypatch, history, expected_capacity,
):
    predictor = SimpleNamespace(name="test")
    if history is not None:
        predictor.history_seconds = history
    report = {"run_id": "test", "feature_profile": "technical_v2" if history else "compact_v1",
              "feature_groups": {"Market trend": ["trend_slope"], "Momentum": ["momentum_rsi"]}}
    monkeypatch.setattr("app.accuracy.service.load_artifact", lambda path: {
        "threshold": .97, "predictor": predictor, "report": report,
    })
    service = AccuracyService(tmp_path)

    async def idle():
        await asyncio.Future()

    monkeypatch.setattr(service, "_run", idle)

    async def scenario():
        status = await service.start()
        assert service.ticks.maxlen == expected_capacity
        assert service.history_seconds == (history or 900)
        assert status["feature_profile"] == report["feature_profile"]
        assert status["feature_groups"] == ["Market trend", "Momentum"]
        await service.stop()

    asyncio.run(scenario())


def test_loading_buffers_ticks_without_resolving_pending_signals(tmp_path):
    service = AccuracyService(tmp_path)
    service.loading_history = True
    service.pending = {"prior": {"decision_epoch": 0, "expiry_epoch": 545}}
    asyncio.run(service.on_tick({"tick": {"epoch": 1200, "quote": 100., "symbol": "R_100"}}))
    assert "prior" in service.pending
    assert service.ticks[-1] == (1200, 100.)
    assert service.last_evaluated == -1
    assert not (tmp_path / "signals.jsonl").exists()


def test_ordinary_tick_does_not_copy_the_full_model_history(tmp_path):
    class _NoIteration(deque):
        def __iter__(self):
            raise AssertionError("A non-decision tick must not rebuild full arrays")

    service = AccuracyService(tmp_path)
    service.ticks = _NoIteration([(1202, 100.)], maxlen=50400)
    asyncio.run(service.on_tick({"tick": {"epoch": 1204, "quote": 100., "symbol": "R_100"}}))
    assert service.last_tick == {"epoch": 1204, "price": 100.}


def test_outcome_path_copy_is_limited_to_that_contract(tmp_path):
    service = AccuracyService(tmp_path)
    service.ticks = deque(((epoch, 100.) for epoch in range(0, 60000, 2)), maxlen=50400)
    epochs, prices = service._outcome_arrays(54000)
    assert epochs[0] == 54000
    assert epochs[-1] == 54550
    assert len(epochs) == len(prices) < 300


@pytest.mark.parametrize("history_has_missing_ticks", [True, False])
def test_gap_callback_is_nonblocking_and_history_restores_only_observed_ticks(
    tmp_path, history_has_missing_ticks,
):
    from app.accuracy.technical import technical_features

    service = AccuracyService(tmp_path)
    service.history_seconds = 43200
    service.ticks = deque(((epoch, 100.) for epoch in range(0, 59990, 2)), maxlen=50400)
    service.client = _HistoryClient()
    if not history_has_missing_ticks:
        service.client.epochs = service.client.epochs[service.client.epochs < 59990]

    async def scenario():
        tick = {"tick": {"epoch": 60000, "quote": 100., "symbol": "R_100"}}
        await asyncio.wait_for(service.on_tick(tick), timeout=.1)
        await service.on_tick(tick)
        assert service.client.requests == []
        assert service.loading_history
        assert service._backfill_requested.is_set()
        assert service._gap_generation == 1
        assert service.last_evaluated == -1
        rows = np.array(service.ticks)
        assert technical_features(rows[:, 0].astype(np.int64), rows[:, 1], 60000) is None

        await service._repair_history()
        assert not service.loading_history
        assert not service._backfill_requested.is_set()
        assert len(service.client.requests) == 1  # Fetch the gap, not all twelve hours again.
        await service.on_tick(tick)
        await service._repair_history()
        assert len(service.client.requests) == 1
        rows = np.array(service.ticks)
        epochs = rows[:, 0].astype(np.int64)
        assert len(epochs) == len(np.unique(epochs))
        ready = technical_features(epochs, rows[:, 1], 60000)
        assert (ready is not None) is history_has_missing_ticks

    asyncio.run(scenario())


def test_new_gap_during_backfill_is_not_lost_or_replayed_twice(tmp_path):
    service = AccuracyService(tmp_path)
    service.history_seconds = 43200
    service.ticks = deque(((epoch, 100.) for epoch in range(0, 59990, 2)), maxlen=50400)

    class _ConcurrentGapClient(_HistoryClient):
        async def get_ticks_history(self, *args, **kwargs):
            response = await super().get_ticks_history(*args, **kwargs)
            if len(self.requests) == 1:
                self.epochs = np.arange(0, 60012, 2)
                await service.on_tick({"tick": {"epoch": 60012, "quote": 100., "symbol": "R_100"}})
            return response

    service.client = _ConcurrentGapClient()

    async def scenario():
        await service.on_tick({"tick": {"epoch": 60000, "quote": 100., "symbol": "R_100"}})
        await service._repair_history()
        assert service._backfill_requested.is_set()
        assert service.loading_history
        assert service._gap_generation == 2
        await service._repair_history()
        assert not service.loading_history
        assert not service._backfill_requested.is_set()
        assert len(service.client.requests) == 2
        epochs = np.array([epoch for epoch, _ in service.ticks])
        assert np.max(np.diff(epochs)) == 2
        assert len(epochs) == len(np.unique(epochs))

    asyncio.run(scenario())


@pytest.mark.parametrize("failures", [1, 3])
def test_history_recovery_retries_with_bounded_backoff(tmp_path, monkeypatch, failures):
    service = AccuracyService(tmp_path)
    service.ticks.append((1200, 100.))
    calls, delays = [], []

    async def load_history(since_epoch=None):
        calls.append(since_epoch)
        if len(calls) <= failures:
            raise ValueError("public endpoint unavailable")

    async def sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(service, "_load_history", load_history)
    monkeypatch.setattr("app.accuracy.service.asyncio.sleep", sleep)

    async def scenario():
        await service.on_tick({"tick": {"epoch": 1210, "quote": 100., "symbol": "R_100"}})
        if failures == 3:
            with pytest.raises(ValueError, match="Tick-history recovery failed"):
                await service._repair_history()
            assert len(calls) == 3
            assert delays == [5., 10.]
            assert service._backfill_requested.is_set()
            assert service.loading_history
        else:
            await service._repair_history()
            assert len(calls) == 2
            assert delays == [5.]
            assert not service.loading_history
            assert service.error is None

    asyncio.run(scenario())


def test_stop_cancels_inflight_recovery_and_disconnects(tmp_path, monkeypatch):
    service = AccuracyService(tmp_path)
    entered, cancelled = asyncio.Event(), asyncio.Event()
    calls = []

    class _Client:
        disconnected = False

        async def connect(self):
            pass

        async def subscribe_ticks(self, symbol, handler):
            service.ticks.append((1200, 100.))
            await handler({"tick": {"epoch": 1210, "quote": 100., "symbol": "R_100"}})

        async def ensure_connected(self):
            pass

        async def disconnect(self):
            self.disconnected = True

    async def load_history(since_epoch=None):
        calls.append(since_epoch)
        if len(calls) == 1:  # Startup finishes; the buffered gap wakes recovery.
            return
        entered.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    client = _Client()
    monkeypatch.setattr("app.deriv.client.DerivWSClient", lambda: client)
    monkeypatch.setattr(service, "_load_history", load_history)

    async def scenario():
        service.task = asyncio.create_task(service._run())
        await asyncio.wait_for(entered.wait(), timeout=1)
        await asyncio.wait_for(service.stop(), timeout=1)
        assert cancelled.is_set()
        assert service.task.done()
        assert client.disconnected
        assert not service.loading_history
        assert service.reason == "Stopped"

    asyncio.run(scenario())

