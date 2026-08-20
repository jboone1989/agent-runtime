"""Local LLM/provider regulation. Failover here, not via LLM."""
from __future__ import annotations

from ..sensing.signal import Signal
from .action import RegulatoryAction
from .assessment import RegulationAssessment
from .base import RegulationDecision
from .context import RegulationContext
from .escalation import Escalation

ERROR_THRESHOLD = 0.5


class ServiceRegulator:
    name = "service"

    def accepts(self, signal: Signal) -> bool:
        return signal.source == "provider_sensor" or signal.kind.startswith("PROVIDER_")

    def assess(self, signals: list[Signal], context: RegulationContext) -> RegulationAssessment:
        relevant = [s for s in signals if self.accepts(s)]
        errors = [s for s in relevant if s.kind == "PROVIDER_ERROR_RATE"]
        severity = max((s.severity for s in errors), default=0.0)
        return RegulationAssessment(
            system=self.name,
            severity=severity,
            summary=f"provider error severity={severity:.2f}",
            signals=relevant,
        )

    def respond(
        self, assessment: RegulationAssessment, context: RegulationContext
    ) -> RegulationDecision:
        if assessment.severity < ERROR_THRESHOLD:
            return RegulationDecision(reason="provider healthy", confidence=0.8, system=self.name)
        error = next(
            (s for s in assessment.signals if s.kind == "PROVIDER_ERROR_RATE"),
            None,
        )
        evidence = [error.signal_id] if error else []
        backup = _healthiest_backup(context)
        if backup is None:
            return RegulationDecision(
                escalations=[
                    Escalation(
                        code="COGNITIVE_CAPABILITY_AT_RISK",
                        severity=assessment.severity,
                        reason="all providers failing",
                        evidence_refs=evidence,
                        suggested_dimension="capability",
                    )
                ],
                reason="no healthy backup",
                confidence=0.85,
                system=self.name,
            )
        return RegulationDecision(
            local_actions=[
                RegulatoryAction(
                    kind="switch_provider",
                    reason="primary provider error rate high",
                    payload={"target": backup},
                ),
                RegulatoryAction(
                    kind="probe_provider",
                    reason="verify backup after switch",
                    payload={"target": backup},
                ),
            ],
            reason="local provider failover",
            confidence=0.9,
            system=self.name,
        )


def _healthiest_backup(context: RegulationContext) -> str | None:
    active = context.providers.active
    best_name = None
    best_err = 1.0
    for name in context.providers.names:
        if name == active:
            continue
        stats = context.world.providers.get(name) or {}
        err = float(stats.get("error_rate", 1.0))
        available = bool(stats.get("available", True))
        if available and err < ERROR_THRESHOLD and err < best_err:
            best_err = err
            best_name = name
    return best_name
