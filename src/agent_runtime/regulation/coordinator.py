from __future__ import annotations

from dataclasses import dataclass, field

from .action import RegulatoryAction
from .base import RegulationDecision
from .escalation import Escalation
from .state import RegulatoryState


@dataclass
class RegulationBundle:
    decisions: list[RegulationDecision] = field(default_factory=list)
    local_actions: list[RegulatoryAction] = field(default_factory=list)
    escalations: list[Escalation] = field(default_factory=list)


class RegulationCoordinator:
    def __init__(self, systems: list) -> None:
        self.systems = list(systems)

    def process(self, signals, context) -> RegulationBundle:
        bundle = RegulationBundle()
        for system in self.systems:
            accepted = [s for s in signals if system.accepts(s)]
            if not accepted:
                continue
            assessment = system.assess(accepted, context)
            decision = system.respond(assessment, context)
            decision.system = system.name
            bundle.decisions.append(decision)
            bundle.local_actions.extend(decision.local_actions)
            bundle.escalations.extend(decision.escalations)
            for update in decision.state_updates:
                context.regulatory_state.blend(**{update.path: float(update.value)})
        _nudge_stress(bundle, context.regulatory_state)
        return bundle


def _nudge_stress(bundle: RegulationBundle, state: RegulatoryState) -> None:
    if bundle.escalations:
        peak = max(e.severity for e in bundle.escalations)
        state.blend(stress=min(1.0, 0.4 + peak), recovery_priority=min(1.0, 0.4 + peak))
    elif bundle.local_actions:
        state.blend(stress=min(1.0, state.stress + 0.05))
    else:
        state.blend(stress=0.15)
