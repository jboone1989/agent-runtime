"""Capability Fabric — inventory, resolver, acquisition, network delegation (v0.2.6).

Phases:
  v0.2.2  model + inventory + gap detection
  v0.2.3  Executive Foundation (PlanStep.required_capabilities)
  v0.2.4  resolver / evaluator / decision (reuse before create)
  v0.2.5  acquisition + verification (sandboxable fixture adapter)
  v0.2.6  network sources + capability autonomy (this version)
"""
from __future__ import annotations

from .acquisition import (
    AcquisitionService,
    AcquisitionTransaction,
    ActivationRecord,
    RollbackRecord,
)
from .evaluator import CapabilityEvaluator
from .gap import CapabilityGap, CapabilityGapDetector, CapabilityGapStatus
from .inventory import BUILTIN_CAPABILITY_IDS, CapabilityInventory
from .invocation import (
    CapabilityInvocationResult,
    ReadCapabilityGateway,
    ShadowCapabilityGateway,
)
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
from .network import (
    AgentNetworkCapabilitySource,
    DelegationService,
    NullCapabilityNetworkPort,
    TaskContract,
    VerificationReceipt,
)
from .policy import CapabilityPolicy
from .records import CapabilityAcquisitionRecord, CapabilityUsageRecord
from .records import CapabilityHistoryRecord
from .lifecycle import (
    CapabilityLifecycleService,
    CapabilityRemovalDependencies,
    RetirementDecision,
)
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
    "AcquisitionService",
    "AcquisitionTransaction",
    "ActivationRecord",
    "AgentNetworkCapabilitySource",
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
    "CapabilityInvocationResult",
    "CapabilityKind",
    "CapabilityLifecycleState",
    "CapabilityNeed",
    "CapabilityPolicy",
    "CapabilityRequirement",
    "CapabilityResolution",
    "CapabilityResolver",
    "CapabilitySearchRecord",
    "CapabilityUsageRecord",
    "CapabilityHistoryRecord",
    "CapabilityLifecycleService",
    "CapabilityRemovalDependencies",
    "RetirementDecision",
    "ReadCapabilityGateway",
    "ShadowCapabilityGateway",
    "DelegationService",
    "FixtureCapabilitySource",
    "LocalCapabilitySource",
    "NullCapabilityNetworkPort",
    "RollbackRecord",
    "SideEffectLevel",
    "TaskContract",
    "VerificationReceipt",
]
