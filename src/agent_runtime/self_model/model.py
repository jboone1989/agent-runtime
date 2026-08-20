"""SelfModel: what belongs to me, what I can do, what might be wrong."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..body.model import BodyModel
from ..identity.identity import AgentIdentity
from ..observations import Observation
from .beliefs import (
    CapabilityBelief,
    IdentityBelief,
    RelationshipBelief,
    ResourceBelief,
    SelfPrediction,
    StateBelief,
    VulnerabilityBelief,
)
from .uncertainty import default_uncertainty


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
        """Update only what belongs to self. Returns changed metric names."""
        changed: list[str] = []
        payload = observation.self_payload()
        for name, value in payload.items():
            prev = self.internal_state.get(name)
            trend = 0.0
            if prev is not None and isinstance(value, (int, float)) and isinstance(prev.value, (int, float)):
                trend = float(value) - float(prev.value)
            unc = default_uncertainty(observation_count=1 if prev is None else 2)
            self.internal_state[name] = StateBelief(
                name=name,
                value=value,
                trend=trend,
                uncertainty=unc,
                last_updated=observation.timestamp,
                evidence_refs=[observation.observation_id],
            )
            self.uncertainty[f"internal_state.{name}"] = unc
            if name in self.resources and isinstance(value, (int, float)):
                res = self.resources[name]
                self.resources[name] = ResourceBelief(
                    name=res.name,
                    amount=float(value),
                    unit=res.unit,
                    uncertainty=unc,
                    evidence_refs=[observation.observation_id],
                )
            changed.append(name)
        return changed

    def apply_update(self, path: str, new_value, reason: str, uncertainty: float, evidence: list[str]) -> None:
        self.uncertainty[path] = uncertainty
        if path.startswith("capabilities."):
            name = path.split(".", 1)[1]
            existing = self.capabilities.get(name)
            available = bool(new_value) if not isinstance(new_value, CapabilityBelief) else new_value.available
            self.capabilities[name] = CapabilityBelief(
                name=name,
                available=available,
                proficiency=existing.proficiency if existing else 0.5,
                uncertainty=uncertainty,
                evidence_refs=evidence,
            )
        elif path.startswith("vulnerabilities."):
            name = path.split(".", 1)[1]
            self.vulnerabilities[name] = VulnerabilityBelief(
                name=name,
                severity=float(new_value) if isinstance(new_value, (int, float)) else 0.5,
                description=reason,
                uncertainty=uncertainty,
                evidence_refs=evidence,
            )

    def sync_goals(self, goal_ids: list[str]) -> None:
        self.goals = list(goal_ids)

    def as_self_state(self, identity: AgentIdentity) -> SelfState:
        metrics: dict[str, float] = {}
        for name, belief in self.internal_state.items():
            if isinstance(belief.value, (int, float)):
                metrics[name] = float(belief.value)
        for name, res in self.resources.items():
            metrics.setdefault(name, res.amount)
        return SelfState(identity=identity, metrics=metrics)
