"""Capability Fabric core model — v0.2.2 foundation.

Distinguishes *what exists* (inventory), *what is missing* (gap),
and *how a missing capability should be obtained* (decision).
No networking, no acquisition, no skill standards — those are later phases.

Decision order is architectural (REUSE_LOCAL first, CREATE last):
    REUSE_LOCAL > DELEGATE_REMOTE > ACQUIRE > ADAPT > CREATE
The enum definition order below locks that priority. Do not reorder.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CapabilityKind(Enum):
    """What kind of thing implements the capability."""

    BUILTIN = "BUILTIN"
    SKILL = "SKILL"
    TOOL = "TOOL"
    MCP = "MCP"
    REMOTE_AGENT = "REMOTE_AGENT"
    LIBRARY = "LIBRARY"
    SERVICE = "SERVICE"
    WORKFLOW = "WORKFLOW"


class SideEffectLevel(Enum):
    """How invasive a capability can be. Ordered by severity."""

    NONE = 0
    READ_ONLY = 1
    LOCAL_STATE = 2
    EXTERNAL_SIDE_EFFECT = 3
    IRREVERSIBLE = 4


class CapabilityAccessMode(Enum):
    """Semantic distinction: 'I own it' vs 'I can call it'.

    A remote agent I can delegate to is NOT a capability I possess.
    Collapsing these would corrupt the SelfModel (Self ≠ World).
    """

    OWNED = "OWNED"
    LOCAL = "LOCAL"
    REMOTE_ACCESS = "REMOTE_ACCESS"
    ACQUIRABLE = "ACQUIRABLE"
    UNAVAILABLE = "UNAVAILABLE"


class CapabilityLifecycleState(Enum):
    """DISCOVERED != SAFE. ACQUIRED != TRUSTED. Only ACTIVE may execute.

    VERIFIED is a checkpoint before activation; it is not executable.
    """

    DISCOVERED = "DISCOVERED"
    INSPECTED = "INSPECTED"
    APPROVED = "APPROVED"
    ACQUIRED = "ACQUIRED"
    VERIFIED = "VERIFIED"
    ACTIVE = "ACTIVE"
    DEGRADED = "DEGRADED"
    QUARANTINED = "QUARANTINED"
    RETIRED = "RETIRED"


class CapabilityDecisionAction(Enum):
    """Resolution strategy. Definition order == priority order."""

    REUSE_LOCAL = 0
    DELEGATE_REMOTE = 1
    ACQUIRE = 2
    ADAPT = 3
    CREATE = 4


# Convenience alias: the canonical priority ladder.
DECISION_PRIORITY: list[CapabilityDecisionAction] = list(CapabilityDecisionAction)

ASSESSMENT_DIMENSIONS: tuple[str, ...] = (
    "task_fit",
    "compatibility",
    "security",
    "provenance",
    "maintenance",
    "health",
    "cost",
    "latency",
    "reversibility",
    "confidence",
)


@dataclass
class CapabilityRequirement:
    """What a plan needs: identity of the capability plus hard constraints."""

    capability_id: str
    intent: str = ""
    required_inputs: dict[str, Any] = field(default_factory=dict)
    required_outputs: dict[str, Any] = field(default_factory=dict)
    constraints: dict[str, Any] = field(default_factory=dict)
    max_side_effect: SideEffectLevel = SideEffectLevel.READ_ONLY
    required_permissions: set[str] = field(default_factory=set)


@dataclass
class CapabilityCandidate:
    """A possible implementation that exists in the world.

    Candidate != Skill: it may be a tool, MCP server, remote agent,
    library, service, or workflow. External claims stay claims — they
    are evidence, never proof.
    """

    candidate_id: str
    capability_id: str
    kind: CapabilityKind
    provider: str
    description: str = ""
    version: str | None = None
    tags: list[str] = field(default_factory=list)
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    permissions: set[str] = field(default_factory=set)
    side_effects: SideEffectLevel = SideEffectLevel.READ_ONLY
    acquisition_mode: str = ""
    source: str = ""
    provenance: dict[str, Any] = field(default_factory=dict)
    license: str | None = None
    evidence_refs: list[str] = field(default_factory=list)


@dataclass
class CapabilityBinding:
    """An inventory entry: how a capability is concretely bound here.

    The inventory records objective fact (installed/connected/healthy);
    SelfModel.capabilities records the agent's *belief* about it.
    """

    capability_id: str
    kind: CapabilityKind
    provider: str
    binding_id: str | None = None
    candidate_id: str | None = None
    access_mode: CapabilityAccessMode = CapabilityAccessMode.LOCAL
    state: CapabilityLifecycleState = CapabilityLifecycleState.ACTIVE
    version: str | None = None
    entry_point: dict[str, Any] = field(default_factory=dict)
    health: float = 1.0
    evidence_refs: list[str] = field(default_factory=list)


@dataclass
class CapabilityAssessment:
    """Multi-dimensional evaluation. Hard constraints filter first;
    dimensions compare later. Never collapse to a single score.
    """

    candidate_id: str
    capability_id: str
    dimensions: dict[str, float] = field(default_factory=dict)
    hard_fail_reasons: list[str] = field(default_factory=list)

    @property
    def viable(self) -> bool:
        return not self.hard_fail_reasons


@dataclass
class CapabilityDecision:
    """The chosen resolution for one capability need.

    CREATE requires prior search evidence (enforced in v0.2.3+).
    """

    capability_id: str
    action: CapabilityDecisionAction
    candidate_id: str | None = None
    rationale: str = ""
    approvals_required: set[str] = field(default_factory=set)
    evidence_refs: list[str] = field(default_factory=list)
    plan_step_id: str | None = None
    search_id: str | None = None
