"""Independent Runtime verification of specialist backend results.

Backend status COMPLETED only means the call finished. This verifier never
treats backend claims, final_response text, or artifacts listed by the
backend as proof that the task succeeded.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, Sequence

from ..backends.model import AgentResult
from .request import VerificationSpec


@dataclass(frozen=True)
class CommandObservation:
    exit_code: int
    stdout: str = ""
    stderr: str = ""


class WorkspaceView(Protocol):
    def has_artifact(self, uri: str) -> bool: ...

    def run_command(
        self, command: Sequence[str], *, cwd: str | None = None
    ) -> CommandObservation: ...


@dataclass(frozen=True)
class VerificationResult:
    status: str
    summary: str
    verified_success: bool = False
    evidence_result: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


class LocalWorkspace:
    """Filesystem view used when no test double is injected."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def has_artifact(self, uri: str) -> bool:
        path = Path(uri)
        if not path.is_absolute():
            path = self.root / path
        try:
            return path.exists()
        except OSError:
            return False

    def run_command(
        self, command: Sequence[str], *, cwd: str | None = None
    ) -> CommandObservation:
        try:
            completed = subprocess.run(
                list(command),
                cwd=str(cwd or self.root),
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return CommandObservation(exit_code=127, stdout="", stderr=str(exc))
        return CommandObservation(
            exit_code=int(completed.returncode),
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )


class RuntimeVerifier:
    """Runtime-owned checks. Backend success is evidence of a call, not a goal."""

    def verify(
        self,
        *,
        expected: VerificationSpec,
        backend_result: AgentResult,
        workspace: WorkspaceView,
    ) -> VerificationResult:
        if backend_result.status == "TIMED_OUT":
            return VerificationResult(
                status="TIMEOUT",
                summary="execution timed out before independent verification",
                verified_success=False,
                evidence_result="UNVERIFIABLE",
            )
        del backend_result  # claims are not an input to verified_success
        if not expected.command and not expected.required_artifacts:
            return VerificationResult(
                status="UNVERIFIABLE",
                summary="no independent verification spec",
                verified_success=False,
                evidence_result="UNVERIFIABLE",
            )

        details: dict[str, Any] = {}
        missing = [
            uri
            for uri in expected.required_artifacts
            if not workspace.has_artifact(uri)
        ]
        if missing:
            details["missing_artifacts"] = missing
            return VerificationResult(
                status="FAIL",
                summary="required artifacts were not present in the workspace",
                verified_success=False,
                evidence_result="COVERED",
                details=details,
            )

        if expected.command:
            observed = workspace.run_command(expected.command)
            details["command"] = list(expected.command)
            details["exit_code"] = observed.exit_code
            details["stdout"] = observed.stdout
            details["stderr"] = observed.stderr
            if observed.exit_code != expected.expected_exit_code:
                summary = observed.stderr.strip() or "verification command failed"
                return VerificationResult(
                    status="FAIL",
                    summary=summary,
                    verified_success=False,
                    evidence_result="COVERED",
                    details=details,
                )

        return VerificationResult(
            status="PASS",
            summary="runtime verification passed",
            verified_success=True,
            evidence_result="COVERED",
            details=details,
        )
