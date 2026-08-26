"""SelfModel: what belongs to me, what I can do, what might be wrong."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from math import isfinite

from ..body.model import BodyModel
from ..identity.identity import AgentIdentity
from ..observations import Observation
from ..clock import new_id
from .beliefs import (
    BeliefRevision,
    BeliefRevisionStatus,
    CapabilityBelief,
    IdentityBelief,
    KnowledgeAssessment,
    RelationshipBelief,
    ResourceBelief,
    SelfPrediction,
    SelfMonitoringEvent,
    StateBelief,
    VulnerabilityBelief,
)
from .uncertainty import default_uncertainty


@dataclass
class SelfObservationResult:
    changed: list[str] = field(default_factory=list)
    revisions: list[BeliefRevision] = field(default_factory=list)
    monitoring_events: list[SelfMonitoringEvent] = field(default_factory=list)


@dataclass
class SelfState:
    """Operational snapshot of *this* agent — never mixed with WorldState."""

    identity: AgentIdentity
    metrics: dict[str, float] = field(default_factory=dict)
    process_id: str | None = None


@dataclass
class SelfModel:
    identity: IdentityBelief
    capabilities: dict[str, CapabilityBelief]
    resources: dict[str, ResourceBelief]
    vulnerabilities: dict[str, VulnerabilityBelief]
    internal_state: dict[str, StateBelief]
    relationships: dict[str, RelationshipBelief]
    goals: list[str]
    history_summary: str
    future_predictions: list[SelfPrediction]
    uncertainty: dict[str, float]
    body: BodyModel = field(default_factory=BodyModel.empty)
    belief_revisions: list[BeliefRevision] = field(default_factory=list)
    monitoring_events: list[SelfMonitoringEvent] = field(default_factory=list)
    knowledge_assessments: list[KnowledgeAssessment] = field(default_factory=list)

    @classmethod
    def bootstrap(cls, identity: AgentIdentity, now, body: BodyModel | None = None) -> SelfModel:
        ident = IdentityBelief(
            agent_id=identity.agent_id,
            genesis_id=identity.genesis_id,
            version=identity.current_version,
            uncertainty=0.0,
        )
        capabilities = {
            "observe": CapabilityBelief("observe", True, 1.0, 0.05),
            "introspect": CapabilityBelief("introspect", True, 1.0, 0.05),
            "maintain": CapabilityBelief("maintain", True, 0.6, 0.3),
            "diagnose": CapabilityBelief("diagnose", True, 0.5, 0.4),
            "llm_provider": CapabilityBelief("llm_provider", True, 0.8, 0.2),
            "advisory_reasoning": CapabilityBelief(
                "advisory_reasoning", False, 0.5, 0.4
            ),
            "structured_reasoning": CapabilityBelief(
                "structured_reasoning", False, 0.5, 0.4
            ),
            "workspace_execution": CapabilityBelief(
                "workspace_execution", False, 0.5, 0.4
            ),
            "web_research": CapabilityBelief("web_research", False, 0.5, 0.4),
            "mcp_access": CapabilityBelief("mcp_access", False, 0.5, 0.4),
            "resumable_agent_session": CapabilityBelief(
                "resumable_agent_session", False, 0.5, 0.4
            ),
        }
        resources = {
            "compute": ResourceBelief("compute", 0.8, "fraction", 0.25),
            "storage": ResourceBelief("storage", 0.8, "fraction", 0.25),
            "memory": ResourceBelief("memory", 0.8, "integrity", 0.2),
        }
        uncertainty = {
            "identity": ident.uncertainty,
            **{f"capabilities.{k}": v.uncertainty for k, v in capabilities.items()},
            **{f"resources.{k}": v.uncertainty for k, v in resources.items()},
        }
        return cls(
            identity=ident,
            capabilities=capabilities,
            resources=resources,
            vulnerabilities={},
            internal_state={},
            relationships={},
            goals=[],
            history_summary="genesis",
            future_predictions=[],
            uncertainty=uncertainty,
            body=body or BodyModel.empty(),
        )

    def update_from_observation(self, observation: Observation) -> list[str]:
        """Compatibility wrapper for evidence-aware self-belief reconciliation."""
        return self.reconcile_observation(observation).changed

    def update_from_observation_blind(self, observation: Observation) -> list[str]:
        """Ablation path: overwrite state without conflict or monitoring behavior."""
        changed: list[str] = []
        for name, value in observation.self_payload().items():
            previous = self.internal_state.get(name)
            explicit = observation.uncertainty_for("self", name)
            uncertainty = (
                explicit
                if explicit is not None
                else default_uncertainty(1 if previous is None else 2)
            )
            refs = [observation.observation_id, *observation.evidence_refs]
            trend = 0.0
            if previous is not None and _is_number(previous.value) and _is_number(value):
                trend = float(value) - float(previous.value)
            self.internal_state[name] = StateBelief(
                name=name,
                value=value,
                trend=trend,
                uncertainty=uncertainty,
                last_updated=observation.timestamp,
                evidence_refs=list(dict.fromkeys(refs)),
            )
            self.uncertainty[f"internal_state.{name}"] = uncertainty
            if name in self.resources and _is_number(value):
                resource = self.resources[name]
                self.resources[name] = ResourceBelief(
                    name=resource.name,
                    amount=float(value),
                    unit=resource.unit,
                    uncertainty=uncertainty,
                    evidence_refs=list(dict.fromkeys(refs)),
                )
            if previous is None or previous.value != value:
                changed.append(name)
        return changed

    def reconcile_observation(self, observation: Observation) -> SelfObservationResult:
        """Reconcile self evidence without silently overwriting contradictions."""
        result = SelfObservationResult()
        payload = observation.self_payload()
        for name, value in payload.items():
            prev = self.internal_state.get(name)
            trend = 0.0
            if prev is not None and _is_number(value) and _is_number(prev.value):
                trend = float(value) - float(prev.value)
            explicit_uncertainty = observation.uncertainty_for("self", name)
            unc = (
                explicit_uncertainty
                if explicit_uncertainty is not None
                else default_uncertainty(observation_count=1 if prev is None else 2)
            )
            refs = [observation.observation_id, *list(observation.evidence_refs)]
            seen: list[str] = []
            for ref in refs:
                if ref not in seen:
                    seen.append(ref)
            path = f"internal_state.{name}"
            if prev is not None and prev.value != value:
                materiality = _materiality(prev.value, value)
                monitoring = SelfMonitoringEvent(
                    event_id=new_id("sme"),
                    path=path,
                    prior_value=prev.value,
                    observed_value=value,
                    materiality=materiality,
                    spontaneous=not observation.external_task,
                    timestamp=observation.timestamp,
                    evidence_refs=seen,
                )
                result.monitoring_events.append(monitoring)
                self.monitoring_events.append(monitoring)
                supporting = [
                    item
                    for item in self.belief_revisions
                    if item.path == path
                    and item.status == BeliefRevisionStatus.OPEN
                    and item.observed_value == value
                    and item.evidence_refs != seen
                ]
                residual_uncertainty = unc
                for item in supporting:
                    residual_uncertainty *= item.observation_uncertainty
                combined_reliability = 1.0 - residual_uncertainty
                original_prior_uncertainty = min(
                    [prev.uncertainty, *[item.prior_uncertainty for item in supporting]]
                )
                prior_reliability = 1.0 - original_prior_uncertainty
                accept = unc <= 0.2 or combined_reliability >= prior_reliability
                status = (
                    BeliefRevisionStatus.CORRECTED
                    if accept
                    else BeliefRevisionStatus.OPEN
                )
                revision = BeliefRevision(
                    revision_id=new_id("brv"),
                    path=path,
                    prior_value=prev.value,
                    observed_value=value,
                    accepted_value=value if accept else prev.value,
                    status=status,
                    timestamp=observation.timestamp,
                    prior_uncertainty=prev.uncertainty,
                    observation_uncertainty=unc,
                    evidence_refs=seen,
                    reason=(
                        "corroborated or stronger evidence revised self-belief"
                        if accept
                        else "contradictory evidence retained for diagnosis"
                    ),
                )
                result.revisions.append(revision)
                self.belief_revisions.append(revision)
                if not accept:
                    prev.uncertainty = min(
                        0.5,
                        prev.uncertainty + 0.1 * (1.0 - unc),
                    )
                    self.uncertainty[path] = prev.uncertainty
                    continue
                for item in self.belief_revisions:
                    if (
                        item is not revision
                        and item.path == path
                        and item.status == BeliefRevisionStatus.OPEN
                    ):
                        item.status = BeliefRevisionStatus.CORRECTED
                        item.accepted_value = value
            self.internal_state[name] = StateBelief(
                name=name,
                value=value,
                trend=trend,
                uncertainty=unc,
                last_updated=observation.timestamp,
                evidence_refs=seen,
            )
            self.uncertainty[path] = unc
            if name in self.resources and _is_number(value):
                res = self.resources[name]
                self.resources[name] = ResourceBelief(
                    name=res.name,
                    amount=float(value),
                    unit=res.unit,
                    uncertainty=unc,
                    evidence_refs=seen,
                )
            if prev is None or prev.value != value:
                result.changed.append(name)
        return result

    def apply_update(
        self,
        path: str,
        new_value,
        reason: str,
        uncertainty: float,
        evidence: list[str],
        *,
        timestamp: datetime | None = None,
    ) -> bool:
        prefix, separator, name = path.partition(".")
        if not separator or not name or prefix not in {
            "capabilities",
            "resources",
            "internal_state",
            "vulnerabilities",
        }:
            return False
        if not _is_number(uncertainty) or not isfinite(uncertainty):
            return False
        if not 0.0 <= float(uncertainty) <= 1.0:
            return False
        if prefix == "resources" and not _is_number(new_value):
            return False
        self.uncertainty[path] = uncertainty
        if prefix == "capabilities":
            existing = self.capabilities.get(name)
            available = (
                bool(new_value)
                if not isinstance(new_value, CapabilityBelief)
                else new_value.available
            )
            self.capabilities[name] = CapabilityBelief(
                name=name,
                available=available,
                proficiency=existing.proficiency if existing else 0.5,
                uncertainty=uncertainty,
                evidence_refs=list(evidence),
            )
        elif prefix == "resources":
            existing = self.resources.get(name)
            self.resources[name] = ResourceBelief(
                name=name,
                amount=float(new_value),
                unit=existing.unit if existing else "fraction",
                uncertainty=uncertainty,
                evidence_refs=list(evidence),
            )
        elif prefix == "internal_state":
            existing = self.internal_state.get(name)
            trend = 0.0
            if (
                existing is not None
                and _is_number(existing.value)
                and _is_number(new_value)
            ):
                trend = float(new_value) - float(existing.value)
            self.internal_state[name] = StateBelief(
                name=name,
                value=new_value,
                trend=trend,
                uncertainty=uncertainty,
                last_updated=timestamp or datetime.now(timezone.utc),
                evidence_refs=list(evidence),
            )
        elif prefix == "vulnerabilities":
            self.vulnerabilities[name] = VulnerabilityBelief(
                name=name,
                severity=float(new_value) if _is_number(new_value) else 0.5,
                description=reason,
                uncertainty=uncertainty,
                evidence_refs=list(evidence),
            )
        return True

    def sync_goals(self, goal_ids: list[str]) -> None:
        self.goals = list(goal_ids)

    def as_self_state(self, identity: AgentIdentity) -> SelfState:
        metrics: dict[str, float] = {}
        for name, belief in self.internal_state.items():
            if _is_number(belief.value):
                metrics[name] = float(belief.value)
        for name, res in self.resources.items():
            metrics.setdefault(name, res.amount)
        return SelfState(identity=identity, metrics=metrics)


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _materiality(prior: Any, observed: Any) -> float:
    if _is_number(prior) and _is_number(observed):
        return min(1.0, abs(float(observed) - float(prior)))
    return 1.0 if prior != observed else 0.0
