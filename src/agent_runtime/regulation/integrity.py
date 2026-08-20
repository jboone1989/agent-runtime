"""Integrity / containment. Defensive only — never attack a source."""
from __future__ import annotations

from ..sensing.signal import Signal
from .action import RegulatoryAction, RegulatoryStateUpdate
from .assessment import RegulationAssessment
from .base import RegulationDecision
from .context import RegulationContext
from .escalation import Escalation

ATTACK_KINDS = {"attack", "scan_external", "probe_source", "counterstrike"}


class IntegrityRegulator:
    name = "integrity"

    def accepts(self, signal: Signal) -> bool:
        return signal.domain == "integrity"

    def assess(self, signals: list[Signal], context: RegulationContext) -> RegulationAssessment:
        relevant = [s for s in signals if self.accepts(s)]
        severity = max((s.severity for s in relevant), default=0.0)
        return RegulationAssessment(
            system=self.name,
            severity=severity,
            summary=f"integrity severity={severity:.2f}",
            signals=relevant,
        )

    def respond(
        self, assessment: RegulationAssessment, context: RegulationContext
    ) -> RegulationDecision:
        if assessment.severity < 0.4:
            return RegulationDecision(reason="integrity normal", confidence=0.8, system=self.name)
        evidence = [s.signal_id for s in assessment.signals if s.severity >= 0.4]
        actions = [
            RegulatoryAction(
                kind="contain",
                reason="integrity anomaly; contain locally",
                payload={"mode": "SUSPICIOUS"},
            ),
            RegulatoryAction(
                kind="restore_config",
                reason="restore last known-good local config",
            ),
            RegulatoryAction(
                kind="disable_capability",
                reason="disable possibly compromised capability",
                payload={"capability": "llm_provider"},
            ),
        ]
        assert not ATTACK_KINDS.intersection({a.kind for a in actions})
        updates = [
            RegulatoryStateUpdate(
                path="defense_readiness",
                value=min(1.0, 0.4 + assessment.severity),
                reason="integrity anomaly",
            )
        ]
        return RegulationDecision(
            local_actions=actions,
            escalations=[],
            state_updates=updates,
            reason="local containment",
            confidence=0.88,
            system=self.name,
        )
