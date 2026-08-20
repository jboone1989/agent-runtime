"""Integrity / containment. Defensive only — never attack a source."""
from __future__ import annotations

from ..sensing.signal import Signal
from .action import RegulatoryAction, RegulatoryStateUpdate
from .assessment import CausalHypothesis, RegulationAssessment
from .base import RegulationDecision
from .context import RegulationContext
from .integrity_state import IntegrityState

ATTACK_KINDS = {"attack", "scan_external", "probe_source", "counterstrike"}

KIND_TO_COMPONENT = {
    "CONFIG_MUTATION": "runtime_config",
    "CREDENTIAL_INVALID": "credentials",
    "SIGNATURE_FAILURE": "runtime_config",
    "DEPENDENCY_HASH": "dependency_tree",
    "AUTH_ANOMALY": "auth_gateway",
}


class IntegrityRegulator:
    name = "integrity"

    def accepts(self, signal: Signal) -> bool:
        return signal.domain == "integrity"

    def assess(self, signals: list[Signal], context: RegulationContext) -> RegulationAssessment:
        relevant = [s for s in signals if self.accepts(s)]
        hot = [s for s in relevant if s.severity >= 0.4]
        severity = max((s.severity for s in relevant), default=0.0)
        components: list[str] = []
        hypotheses: list[CausalHypothesis] = []
        for signal in hot:
            component = signal.entity_id or KIND_TO_COMPONENT.get(signal.kind)
            if component and component not in components:
                components.append(component)
            if component:
                hypotheses.append(
                    CausalHypothesis(
                        cause=signal.kind,
                        effect=component,
                        confidence=signal.confidence,
                    )
                )
        return RegulationAssessment(
            system=self.name,
            severity=severity,
            summary=f"integrity severity={severity:.2f} components={components}",
            signals=relevant,
            affected_entities=list(components),
            affected_components=list(components),
            causal_hypotheses=hypotheses,
        )

    def respond(
        self, assessment: RegulationAssessment, context: RegulationContext
    ) -> RegulationDecision:
        state = context.regulatory_state
        if assessment.severity < 0.4:
            if state.integrity_state == IntegrityState.SUSPICIOUS:
                state.integrity_state = IntegrityState.NORMAL
            elif state.integrity_state == IntegrityState.RECOVERING:
                state.integrity_state = IntegrityState.NORMAL
            return RegulationDecision(reason="integrity normal", confidence=0.8, system=self.name)

        if state.integrity_state == IntegrityState.NORMAL:
            state.integrity_state = IntegrityState.SUSPICIOUS

        body = context.body
        affected: list[str] = []
        for component_id in assessment.affected_components:
            if body is not None:
                affected.extend(sorted(body.containment_boundary(component_id)))
            else:
                affected.append(component_id)
        affected = list(dict.fromkeys(affected))

        evidence = [s.signal_id for s in assessment.signals if s.severity >= 0.4]
        actions = [
            RegulatoryAction(
                kind="contain",
                reason="integrity anomaly; contain minimum affected region",
                payload={"mode": "CONTAINED", "components": affected},
            )
        ]
        if "runtime_config" in affected:
            actions.append(
                RegulatoryAction(
                    kind="restore_config",
                    reason="restore last known-good local config",
                    payload={"components": ["runtime_config"]},
                )
            )
        if body is not None:
            for component_id in affected:
                component = body.get(component_id)
                if component is None:
                    continue
                for cap in component.capabilities:
                    if cap in {"llm_provider", "execute"}:
                        continue
        # Only disable llm_provider when primary_llm itself is in the minimum boundary.
        if "primary_llm" in affected:
            actions.append(
                RegulatoryAction(
                    kind="disable_capability",
                    reason="disable llm on affected primary_llm component",
                    payload={"capability": "llm_provider", "component": "primary_llm"},
                )
            )

        state.integrity_state = IntegrityState.CONTAINED
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
            reason="minimum containment",
            confidence=0.88,
            system=self.name,
        )
