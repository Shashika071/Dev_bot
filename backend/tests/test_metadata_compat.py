"""Model metadata compatibility checks for worker reload."""

from types import SimpleNamespace

from app.worker import _metadata_compatible


def _conf(symbol="R_100", barrier="0.09", duration=540):
    return SimpleNamespace(
        symbol=symbol,
        barrier_input=barrier,
        duration_seconds=duration,
    )


def test_compatible_meta():
    meta = {
        "symbol": "R_100",
        "barrier_distance": 0.09,
        "duration_seconds": 540,
    }
    assert _metadata_compatible(meta, _conf()) is True


def test_rejects_symbol_mismatch():
    meta = {"symbol": "R_50", "barrier_distance": 0.09, "duration_seconds": 540}
    assert _metadata_compatible(meta, _conf()) is False


def test_rejects_barrier_mismatch():
    meta = {"symbol": "R_100", "barrier_distance": 0.15, "duration_seconds": 540}
    assert _metadata_compatible(meta, _conf()) is False


def test_rejects_duration_mismatch():
    meta = {"symbol": "R_100", "barrier_distance": 0.09, "duration_seconds": 300}
    assert _metadata_compatible(meta, _conf()) is False


def test_rejects_empty():
    assert _metadata_compatible({}, _conf()) is False
    assert _metadata_compatible(None, _conf()) is False
