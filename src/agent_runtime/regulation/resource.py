"""Metabolic-style resource regulation. Do not ask an LLM first."""
from __future__ import annotations

from ..sensing.signal import Signal
from .action import RegulatoryAction, RegulatoryStateUpdate
from .assessment import RegulationAssessment
from .base import RegulationDecision
from .context import RegulationContext
from .escalation import Escalation

CONSERVE_AT = 0.45
DANGER_REMAINING = 0.20

KIND_TO_DIMENSION = {
    "COMPUTE": "compute",
    "MEMORY": "memory_integrity",
    "MEMORY_USAGE": "memory_integrity",
    "DISK": "storage",
    "TOKEN_BUDGET": "financial_resources",
    "FINANCIAL": "financial_resources",
    "NETWORK": "network_connectivity",
    "BATTERY": "energy",
    "TEMPERATURE": "operational_integrity",
}


class ResourceRegulator:
    name = "resource"

    def accepts(self, signal: Signal) -> bool:
        return signal.domain == "resource"

    def assess(self, signals: list[Signal], context: RegulationContext) -> RegulationAssessment:
        relevant = [s for s in signals if self.accepts(s)]
        severity = max((s.severity for s in relevant), default=0.0)
        entities = [s.entity_id or s.kind for s in relevant if s.severity >= CONSERVE_AT]
        return RegulationAssessment(
            system=self.name,
            severity=severity,
            summary=f"resource shortage={severity:.2f}",
            signals=relevant,
            affected_entities=entities,
            affected_components=entities,
        )

    def respond(
        self, assessment: RegulationAssessment, context: RegulationContext
    ) -> RegulationDecision:
        if assessment.severity < CONSERVE_AT:
            return RegulationDecision(reason="resources healthy", confidence=0.7, system=self.name)
        low = [
            s
            for s in assessment.signals
            if isinstance(s.value, (int, float)) and float(s.value) <= DANGER_REMAINING
        ]
        actions = [
            RegulatoryAction(
                kind="conserve",
                reason="resource shortage; reduce nonessential work",
                payload={"suspend_user_goals": True},
            )
        ]
        updates = [
            RegulatoryStateUpdate(
                path="resource_conservation",
                value=min(1.0, 0.55 + assessment.severity),
                reason="shortage",
            )
        ]
        escalations = []
        for signal in low:
            dimension = KIND_TO_DIMENSION.get(signal.kind, signal.kind.lower())
            escalations.append(
                Escalation(
                    code="RESOURCE_SURVIVAL_RISK",
                    severity=max(0.7, assessment.severity, signal.severity),
                    reason=f"{signal.kind} critically low",
                    evidence_refs=[signal.signal_id],
                    suggested_dimension=dimension,
                )
            )
        return RegulationDecision(
            local_actions=actions,
            escalations=escalations,
            state_updates=updates,
            reason="conservation mode",
            confidence=0.85,
            system=self.name,
        )
