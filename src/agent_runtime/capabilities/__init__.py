"""Capability Fabric — v0.2.2 foundation (local inventory, needs, gaps, policy).

Phases:
  v0.2.2  model + inventory + gap detection (this version, no acquisition)
  v0.2.3  resolver / evaluator / decision (reuse before create)
  v0.2.4  acquisition + verification (skills, providers, MCP)
  v0.2.5  network sources + capability autonomy
"""
from __future__ import annotations

from .gap import CapabilityGap, CapabilityGapDetector, CapabilityGapStatus
from .inventory import BUILTIN_CAPABILITY_IDS, CapabilityInventory
from .model import (
    ASSESSMENT_DIMENSIONS,
    DECISION_PRIORITY,
    CapabilityAccessMode,
    CapabilityAssessment,
    CapabilityBinding,
    CapabilityCandidate,
    CapabilityDecision,
    CapabilityDecisionAction,
    CapabilityKind,
    CapabilityLifecycleState,
    CapabilityRequirement,
    SideEffectLevel,
)
from .need import CapabilityNeed
from .policy import CapabilityPolicy
from .records import CapabilityAcquisitionRecord, CapabilityUsageRecord

__all__ = [
    "ASSESSMENT_DIMENSIONS",
    "BUILTIN_CAPABILITY_IDS",
    "DECISION_PRIORITY",
    "CapabilityAccessMode",
    "CapabilityAcquisitionRecord",
    "CapabilityAssessment",
    "CapabilityBinding",
    "CapabilityCandidate",
    "CapabilityDecision",
    "CapabilityDecisionAction",
    "CapabilityGap",
    "CapabilityGapDetector",
    "CapabilityGapStatus",
    "CapabilityInventory",
    "CapabilityKind",
    "CapabilityLifecycleState",
    "CapabilityNeed",
    "CapabilityPolicy",
    "CapabilityRequirement",
    "CapabilityUsageRecord",
    "SideEffectLevel",
]
