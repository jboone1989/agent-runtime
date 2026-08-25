"""Evidence verifiability classification — separate from actuator success.

An action that runs without exception is an actuator success. Whether the
action produced checkable evidence is a different, observable question.
Only successful diagnoses are classified; execution failures carry no
evidence result at all, so failure is never conflated with missing evidence.
"""
from __future__ import annotations

from enum import Enum
from typing import Any


class DiagnosticEvidenceResult(str, Enum):
    COVERED = "COVERED"
    UNCOVERED = "UNCOVERED"
    UNVERIFIABLE = "UNVERIFIABLE"


def classify_diagnostic_evidence(
    effects: dict[str, Any],
) -> tuple[DiagnosticEvidenceResult, list[str]]:
    missing: list[str] = []
    if effects.get("value") is None:
        missing.append("value")
    if effects.get("uncertainty") is None:
        missing.append("uncertainty")
    if not effects.get("evidence_refs"):
        missing.append("evidence_refs")
    if not missing:
        return DiagnosticEvidenceResult.COVERED, []
    if "value" in missing and "evidence_refs" in missing:
        return DiagnosticEvidenceResult.UNVERIFIABLE, missing
    return DiagnosticEvidenceResult.UNCOVERED, missing
