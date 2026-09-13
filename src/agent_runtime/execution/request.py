"""Inputs for Runtime-owned specialist execution."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VerificationSpec:
    """Independent checks. Backend claims never satisfy this spec."""

    command: tuple[str, ...] = ()
    expected_exit_code: int = 0
    required_artifacts: tuple[str, ...] = ()

    @classmethod
    def from_payload(cls, payload: dict | None) -> VerificationSpec:
        raw = {} if payload is None else payload.get("verification") or {}
        command = raw.get("command") or ()
        artifacts = raw.get("required_artifacts") or ()
        return cls(
            command=tuple(command),
            expected_exit_code=int(raw.get("expected_exit_code") or 0),
            required_artifacts=tuple(artifacts),
        )
