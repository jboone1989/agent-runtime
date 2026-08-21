"""Capability Fabric — local inventory, resolver (v0.2.4), no acquisition yet.

Phases:
  v0.2.2  model + inventory + gap detection
  v0.2.3  Executive Foundation (PlanStep.required_capabilities)
  v0.2.4  resolver / evaluator / decision (reuse before create, this version)
  v0.2.5  acquisition + verification (skills, providers, MCP)
  v0.2.6  network sources + capability autonomy
"""
from __future__ import annotations

from .evaluator import CapabilityEvaluator
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
from .resolver import (
    CapabilityCreateWithoutSearchError,
    CapabilityResolution,
    CapabilityResolver,
)
from .search import CapabilitySearchRecord
from .sources.fixture import FixtureCapabilitySource
from .sources.local import LocalCapabilitySource

__all__ = [
    "ASSESSMENT_DIMENSIONS",
    "BUILTIN_CAPABILITY_IDS",
    "DECISION_PRIORITY",
    "CapabilityAccessMode",
    "CapabilityAcquisitionRecord",
    "CapabilityAssessment",
    "CapabilityBinding",
    "CapabilityCandidate",
    "CapabilityCreateWithoutSearchError",
    "CapabilityDecision",
    "CapabilityDecisionAction",
    "CapabilityEvaluator",
    "CapabilityGap",
    "CapabilityGapDetector",
    "CapabilityGapStatus",
    "CapabilityInventory",
    "CapabilityKind",
    "CapabilityLifecycleState",
    "CapabilityNeed",
    "CapabilityPolicy",
    "CapabilityRequirement",
    "CapabilityResolution",
    "CapabilityResolver",
    "CapabilitySearchRecord",
    "CapabilityUsageRecord",
    "FixtureCapabilitySource",
    "LocalCapabilitySource",
    "SideEffectLevel",
]
