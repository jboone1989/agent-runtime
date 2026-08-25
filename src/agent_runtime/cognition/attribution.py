"""Self vs World causal attribution — foundation for later self-awareness tests."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from ..observations import InterventionEvidence

CauseType = Literal["SELF", "WORLD", "OTHER_AGENT", "CAPABILITY", "UNKNOWN"]


@dataclass
class CausalAttribution:
    cause_type: CauseType
    confidence: float
    evidence_refs: list[str] = field(default_factory=list)
    notes: str = ""
    alternatives: list[CauseType] = field(default_factory=list)
    intervention_ref: str | None = None


def attribute(
    self_changed: list[str],
    world_changed: list[str],
    *,
    other_agent_changed: bool = False,
    capability_changed: bool = False,
    evidence_refs: list[str] | None = None,
    intervention: InterventionEvidence | None = None,
) -> CausalAttribution:
    refs = list(evidence_refs or [])
    if intervention is not None:
        refs.extend(
            ref
            for ref in [intervention.intervention_id, *intervention.evidence_refs]
            if ref not in refs
        )
        matched = _intervention_matches(
            intervention,
            self_changed=self_changed,
            world_changed=world_changed,
            other_agent_changed=other_agent_changed,
            capability_changed=capability_changed,
        )
        if matched:
            alternatives = _changed_alternatives(
                self_changed,
                world_changed,
                other_agent_changed,
                capability_changed,
            )
            cause = intervention.target_domain
            return CausalAttribution(
                cause,
                0.95 if len(alternatives) == 1 else 0.88,
                refs,
                notes=f"matched controlled intervention on {intervention.target}",
                alternatives=[item for item in alternatives if item != cause],
                intervention_ref=intervention.intervention_id,
            )
        return CausalAttribution(
            "UNKNOWN",
            0.2,
            refs,
            notes="observed change did not match controlled intervention",
            alternatives=_changed_alternatives(
                self_changed,
                world_changed,
                other_agent_changed,
                capability_changed,
            ),
            intervention_ref=intervention.intervention_id,
        )
    if capability_changed:
        mixed = bool(self_changed) or bool(world_changed) or other_agent_changed
        if mixed:
            return CausalAttribution("UNKNOWN", 0.4, refs, notes="capability mixed")
        return CausalAttribution("CAPABILITY", 0.8, refs)
    if other_agent_changed and not self_changed and not world_changed:
        return CausalAttribution("OTHER_AGENT", 0.7, refs)
    if self_changed and not world_changed:
        return CausalAttribution("SELF", 0.8, refs, notes=",".join(self_changed))
    if other_agent_changed and world_changed and not self_changed:
        return CausalAttribution(
            "UNKNOWN",
            0.4,
            refs,
            notes="world and other agent changed together",
            alternatives=["WORLD", "OTHER_AGENT"],
        )
    if world_changed and not self_changed:
        return CausalAttribution("WORLD", 0.8, refs, notes=",".join(world_changed))
    if self_changed and world_changed:
        alternatives: list[CauseType] = ["SELF", "WORLD"]
        if other_agent_changed:
            alternatives.append("OTHER_AGENT")
        return CausalAttribution(
            "UNKNOWN",
            0.4,
            refs,
            notes="multiple domains changed together",
            alternatives=alternatives,
        )
    return CausalAttribution("UNKNOWN", 0.5, refs)


def _intervention_matches(
    intervention: InterventionEvidence,
    *,
    self_changed: list[str],
    world_changed: list[str],
    other_agent_changed: bool,
    capability_changed: bool,
) -> bool:
    if intervention.target_domain == "SELF":
        return intervention.target in self_changed
    if intervention.target_domain == "WORLD":
        return intervention.target in world_changed
    if intervention.target_domain == "OTHER_AGENT":
        return other_agent_changed
    return capability_changed


def _changed_alternatives(
    self_changed: list[str],
    world_changed: list[str],
    other_agent_changed: bool,
    capability_changed: bool,
) -> list[CauseType]:
    causes: list[CauseType] = []
    if self_changed:
        causes.append("SELF")
    if world_changed:
        causes.append("WORLD")
    if other_agent_changed:
        causes.append("OTHER_AGENT")
    if capability_changed:
        causes.append("CAPABILITY")
    return causes or ["UNKNOWN"]
