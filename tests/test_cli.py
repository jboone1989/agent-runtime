"""Runnable command-line sessions use and preserve the real Runtime state."""
from __future__ import annotations

import json

import pytest

from agent_runtime.__main__ import _should_run_exit_probe, build_parser, main


def test_cli_bootstraps_then_restores_same_agent(tmp_path, capsys) -> None:
    state = tmp_path / "state"
    assert main(["--state-dir", str(state), "--ticks", "2"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["mode"] == "genesis"
    assert first["cycles_run"] == 2
    assert first["end_cycle"] == 2
    assert first["verified_learning_evidence"] == 2
    assert first["capability_usage"] == 2

    assert main(["--state-dir", str(state), "--ticks", "1"]) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["mode"] == "restored"
    assert second["agent_id"] == first["agent_id"]
    assert second["start_cycle"] == 2
    assert second["end_cycle"] == 3
    assert second["verified_learning_evidence"] == 3
    assert second["capability_usage"] == 3


def test_cli_can_bootstrap_mock_physical_agent(tmp_path, capsys) -> None:
    assert main(
        ["--state-dir", str(tmp_path / "physical"), "--ticks", "1", "--physical"]
    ) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["embodiment"] == "mock_physical"
    assert summary["cycles_run"] == 1


@pytest.mark.parametrize(
    "flag,value",
    [
        ("--ticks", "0"),
        ("--idle-seconds", "-1"),
        ("--checkpoint-every", "0"),
        ("--max-consecutive-errors", "0"),
        ("--journal-every", "0"),
        ("--reflection-every", "-1"),
        ("--reflection-max-tokens", "63"),
    ],
)
def test_cli_rejects_invalid_run_limits(flag, value, tmp_path) -> None:
    with pytest.raises(SystemExit):
        main(["--state-dir", str(tmp_path / "state"), flag, value])


def test_forever_reflection_mode_does_not_add_an_exit_probe() -> None:
    parser = build_parser()
    assert _should_run_exit_probe(parser.parse_args(["--forever", "--llm"])) is False
    assert _should_run_exit_probe(parser.parse_args(["--llm"])) is True
    assert (
        _should_run_exit_probe(
            parser.parse_args(["--forever", "--llm-prompt", "explicit final probe"])
        )
        is True
    )
