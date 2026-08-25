from __future__ import annotations

from agent_runtime.awareness import run_benchmark


def test_short_benchmark_scores_suites_but_does_not_accept(tmp_path) -> None:
    report = run_benchmark(tmp_path, independent_trials=1, cycles_per_trial=12)

    assert len(report.suite_scores) == 8
    assert report.minimum_causal_efficacy_gap >= 0.2
    assert report.accepted is False
    assert "fewer than three independent trials" in report.acceptance_failures
    assert "fewer than 100 cycles per trial" in report.acceptance_failures
