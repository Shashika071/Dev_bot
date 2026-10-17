"""Chart events update once; history hydration never replays signal alerts."""

import asyncio
import json

import numpy as np
import pytest

from app.accuracy.core import SignalPolicy, features
from app.accuracy.service import AccuracyService
from app.accuracy.training import Predictor
from app.notifications import browser


class _Socket:
    def __init__(self):
        self.messages = []

    async def send_text(self, message):
        self.messages.append(json.loads(message))


@pytest.fixture
def chart_channels(monkeypatch):
    alerts, market = _Socket(), _Socket()
    monkeypatch.setattr(browser, "_channels", {
        "notifications": {alerts}, "market": {market}, "status": set(),
    })
    monkeypatch.setattr("app.accuracy.service.time.time", lambda: 1200)
    return alerts, market


def _service(tmp_path):
    service = AccuracyService(tmp_path)
    epochs = np.arange(0, 1200, 2)
    prices = np.full(len(epochs), 100.)
    names = list(features(np.append(epochs, 1200), np.append(prices, 100.), 1200))
    service.ticks.extend(zip(epochs.tolist(), prices.tolist()))
    service.policy = SignalPolicy()
    service.bundle = {
        "predictor": Predictor("frequency", names, (.97, .96)),
        "report": {"run_id": "test"},
    }
    return service


def test_new_signal_reaches_chart_and_alert_channels_once(tmp_path, chart_channels):
    alerts, market = chart_channels
    service = _service(tmp_path)
    tick = {"tick": {"epoch": 1200, "quote": 100., "symbol": "R_100"}}
    asyncio.run(service.on_tick(tick))
    asyncio.run(service.on_tick(tick))

    assert [event["type"] for event in alerts.messages] == ["accuracy_signal"]
    assert [event["type"] for event in market.messages] == ["accuracy_signal"]
    signal = market.messages[0]["data"]
    assert signal == alerts.messages[0]["data"]
    assert signal["symbol"] == "R_100"
    assert signal["decision_epoch"] == 1200
    assert signal["reference_price"] == 100.
    assert signal["barrier_input"] == "+0.01"
    assert signal["resolved"] is False


@pytest.mark.parametrize("outcome", [True, False, None])
def test_outcome_updates_chart_and_restored_history_without_alert_replay(
    tmp_path, chart_channels, outcome,
):
    alerts, market = chart_channels
    service = _service(tmp_path)
    asyncio.run(service.on_tick({"tick": {"epoch": 1200, "quote": 100., "symbol": "R_100"}}))
    sid = market.messages[0]["data"]["signal_id"]
    # Seed the remaining observed path. One real callback at expiry resolves it.
    for epoch in range(1202, 1746, 2):
        if outcome is None and 1300 <= epoch <= 1310:
            continue
        price = 100.01 if outcome is True and epoch == 1210 else 100.
        service.ticks.append((epoch, price))
    final_tick = {"tick": {"epoch": 1746, "quote": 100., "symbol": "R_100"}}
    asyncio.run(service.on_tick(final_tick))
    asyncio.run(service.on_tick(final_tick))

    assert [event["type"] for event in alerts.messages] == ["accuracy_signal"]
    assert [event["type"] for event in market.messages] == ["accuracy_signal", "accuracy_outcome"]
    result = market.messages[-1]["data"]
    assert result["signal_id"] == sid
    assert result["symbol"] == "R_100"
    assert result["touched"] is outcome
    assert result["outcome"] is outcome
    assert result["resolved"] is True
    assert result["resolved_epoch"] == 1746
    assert result["decision_epoch"] == 1200
    assert sid not in service.pending

    records = [json.loads(line) for line in (tmp_path / "signals.jsonl").read_text().splitlines()]
    assert [record["type"] for record in records] == ["signal", "outcome"]
    assert records[-1]["touched"] is outcome
    # Model/report discovery hydrates chart history on GET status, before Start.
    run = tmp_path / "test"
    run.mkdir()
    (tmp_path / "latest.json").write_text(json.dumps({"run_id": "test"}), encoding="utf-8")
    (run / "report.json").write_text(
        json.dumps({"run_id": "test", "selection": {"threshold": .95}}), encoding="utf-8",
    )
    restored = AccuracyService(tmp_path)
    status = restored.status()
    assert status["running"] is False
    assert status["signals_today"] == 1
    assert len(status["signals"]) == 1
    signal = status["signals"][0]
    assert signal["signal_id"] == sid
    assert signal["outcome"] is outcome
    assert signal["resolved"] is True
    assert signal["resolved_epoch"] == 1746
    assert restored.pending == {}
    assert len(alerts.messages) == 1
    assert len(market.messages) == 2

