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
        errors = [
            s
            for s in relevant
            if s.kind == "PROVIDER_ERROR_RATE" and bool(s.metadata.get("active", True))
        ]
        if not errors:
            errors = [s for s in relevant if s.kind == "PROVIDER_ERROR_RATE"]
        severity = max((s.severity for s in errors), default=0.0)
        entities = [s.entity_id for s in errors if s.entity_id]
        return RegulationAssessment(
            system=self.name,
            severity=severity,
            summary=f"provider error severity={severity:.2f}",
            signals=relevant,
            affected_entities=entities,
            affected_components=["primary_llm"] if severity >= ERROR_THRESHOLD else [],
        )

    def respond(
        self, assessment: RegulationAssessment, context: RegulationContext
    ) -> RegulationDecision:
        if assessment.severity < ERROR_THRESHOLD:
            return RegulationDecision(reason="provider healthy", confidence=0.8, system=self.name)
        error = next(
            (s for s in assessment.signals if s.kind == "PROVIDER_ERROR_RATE" and s.metadata.get("active", True)),
            None,
        )
        if error is None:
            error = next((s for s in assessment.signals if s.kind == "PROVIDER_ERROR_RATE"), None)
        evidence = [error.signal_id] if error else []
        backup = _healthiest_backup(assessment, context)
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


def _healthiest_backup(assessment: RegulationAssessment, context: RegulationContext) -> str | None:
    active = context.environment.read("active_provider")
    rates: dict[str, float] = {}
    available: dict[str, bool] = {}
    for signal in assessment.signals:
        if not signal.entity_id:
            continue
        if signal.kind == "PROVIDER_ERROR_RATE":
            rates[signal.entity_id] = float(signal.value)
        if signal.kind == "PROVIDER_AVAILABLE":
            available[signal.entity_id] = bool(signal.value)
    providers = context.environment.read("providers") or {}
    names = list(providers) if isinstance(providers, dict) else list(rates)
    configured = context.environment.read("provider_names")
    if isinstance(configured, (list, tuple, set)):
        allowed = {str(name) for name in configured}
        names = [name for name in names if name in allowed]
    best_name = None
    best_err = 1.0
    for name in names:
        if name == active:
            continue
        err = float(rates.get(name, (providers.get(name) or {}).get("error_rate", 1.0)))
        is_up = available.get(name, bool((providers.get(name) or {}).get("available", True)))
        if is_up and err < ERROR_THRESHOLD and err < best_err:
            best_err = err
            best_name = name
    return best_name
