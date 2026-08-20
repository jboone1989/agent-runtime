"""Rule-based DefaultMotivationEngine — v0.1 does not attach VirtualBrain."""
from __future__ import annotations

from ..cognition.world_model import WorldState
from ..memory.view import MemoryView
from ..observations import Event
from ..self_model.model import SelfModel
from ..viability.state import ViabilityAssessment
from .engine import ActionTendency, MotivationState
from .tendencies import tendencies_from_viability


class DefaultMotivationEngine:
    def process(
        self,
        *,
        self_model: SelfModel,
        viability: ViabilityAssessment,
        world_state: WorldState,
        event: Event | None,
        memory: MemoryView,
    ) -> MotivationState:
        threat = max((t.severity for t in viability.threats), default=0.0)
        opportunity = max((o.value for o in viability.opportunities), default=0.0)
        urgency = viability.urgency
        valence = max(-1.0, min(1.0, opportunity - threat))
        arousal = max(-1.0, min(1.0, urgency + 0.5 * threat))
        tendencies, updates, drives, attention = tendencies_from_viability(viability, self_model)
        if not tendencies:
            tendencies.append(ActionTendency("introspect", 0.2, "routine self-check"))
        learning = min(1.0, abs(threat - opportunity) * 0.5)
        _ = (world_state, event, memory)
        return MotivationState(
            valence=valence,
            arousal=arousal,
            urgency=urgency,
            threat=threat,
            opportunity=opportunity,
            drives=drives,
            attention_bias=attention,
            action_tendencies=sorted(tendencies, key=lambda x: x.strength, reverse=True),
            learning_signal=learning,
            self_model_updates=updates,
        )
