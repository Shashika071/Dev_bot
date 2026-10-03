"""Training edge criteria use quote breakeven + margin."""

from types import SimpleNamespace

from app.ml.trainer import TrainingOrchestrator


def test_edge_requires_quote_coverage():
    orch = TrainingOrchestrator()
    report = SimpleNamespace(
        selected_signal_count=40,
        selected_win_rate=0.99,
        selected_win_rate_ci_lower=0.98,
        selected_win_rate_ci_upper=0.995,
    )
    ok, reason = orch._compute_edge(report, mean_breakeven=0.95, quote_match_rate=0.1)
    assert ok is False
    assert "quote coverage" in reason.lower()


def test_edge_passes_with_quotes_and_ci():
    orch = TrainingOrchestrator()
    report = SimpleNamespace(
        selected_signal_count=40,
        selected_win_rate=0.99,
        selected_win_rate_ci_lower=0.98,
        selected_win_rate_ci_upper=0.995,
    )
    ok, reason = orch._compute_edge(
        report, mean_breakeven=0.95, quote_match_rate=0.8, wf_fold_count=3
    )
    assert ok is True
    assert "Edge supported" in reason
