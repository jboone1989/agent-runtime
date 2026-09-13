"""Optional VirtualBrain adapter. This package does not import virtualbrain."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from ..cognition.world_model import WorldState
from ..memory.view import MemoryView
from ..motivation.engine import ActionTendency, MotivationState, SelfModelUpdate
from ..motivation.tendencies import tendencies_from_viability
from ..observations import Event
from ..self_model.model import SelfModel
from ..viability.state import ViabilityAssessment
from .legacy_tendency_interpreter import action_tendencies_from_contract


class VirtualBrainLike(Protocol):
    """Duck-typed research brain. Runtime never depends on virtualbrain internals."""

    def pad(self) -> tuple[float, float, float]: ...

    def threat_level(self) -> float: ...

    def opportunity_level(self) -> float: ...

    def needs_pressures(self) -> dict[str, float]: ...

    def emotion_label(self) -> str: ...

    def rpe(self) -> float: ...

    def ingest(self, event: Event | None, now: datetime) -> None: ...


class VirtualPersonBridge:
    """Adapts a VirtualPerson (or any object with the same surface) without importing it."""

    def __init__(self, person: Any, provider: Any | None = None) -> None:
        self.person = person
        self.provider = provider

    def pad(self) -> tuple[float, float, float]:
        return self.person.pad

    def threat_level(self) -> float:
        return float(self.person.threat)

    def opportunity_level(self) -> float:
        last = self._last_appraisal()
        if last is None:
            return 0.0
        return float(last.get("opportunity_level") or 0.0)

    def needs_pressures(self) -> dict[str, float]:
        return dict(self.person.needs.pressures(self.person.conc))

    def emotion_label(self) -> str:
        emotion = self.person.emotion
        return emotion.label if hasattr(emotion, "label") else emotion[0]

    def rpe(self) -> float:
        last = self._last_appraisal()
        if last is None:
            return 0.0
        return float(last.get("reward_delta") or 0.0)

    def ingest(self, event: Event | None, now: datetime) -> None:
        if self.provider is None:
            return
        texts = [event.description] if event is not None else []
        naive = now.replace(tzinfo=None) if now.tzinfo is not None else now
        self.person.tick(naive, sleeping=False, event_texts=texts, provider=self.provider)

    def _last_appraisal(self) -> dict[str, Any] | None:
        log = getattr(self.person, "event_log", None)
        if not log:
            return None
        raw = log[-1].get("appraisal")
        return raw if isinstance(raw, dict) else None


class ContractV1SessionBridge:
    """Adapt the public ContractV1AgencySession surface for Runtime motivation."""

    def __init__(self, session: Any) -> None:
        self.session = session
        self._last_readout: dict[str, Any] = {}

    def _snapshot(self) -> dict[str, Any]:
        snapshot = self.session.snapshot()
        return snapshot if isinstance(snapshot, dict) else {}

    def _affect(self) -> dict[str, Any]:
        affect = self._snapshot().get("affect")
        return affect if isinstance(affect, dict) else {}

    def pad(self) -> tuple[float, float, float]:
        affect = self._affect()
        return (
            float(affect.get("valence") or 0.0),
            float(affect.get("arousal") or 0.0),
            float(affect.get("dominance") or 0.0),
        )

    def threat_level(self) -> float:
        return float(self._affect().get("threat") or 0.0)

    def opportunity_level(self) -> float:
        return float(self._last_readout.get("drives", {}).get("intrinsic_curiosity") or 0.0)

    def needs_pressures(self) -> dict[str, float]:
        return {
            str(key): float(value)
            for key, value in (self._last_readout.get("drives") or {}).items()
        }

    def emotion_label(self) -> str:
        return str(getattr(self.session, "affect_name", "viability"))

    def rpe(self) -> float:
        return float(self._last_readout.get("learning_signal") or 0.0)

    def ingest(self, event: Event | None, now: datetime) -> None:
        _ = (event, now)

    def idle_tick(self, *, world_state: dict[str, Any], event: Event | None) -> None:
        recent_events = () if event is None else (
            {
                "kind": event.kind,
                "description": event.description,
                "data": dict(event.data),
            },
        )
        readout = self.session.idle_tick(world_state=world_state, recent_events=recent_events)
        self._last_readout = readout if isinstance(readout, dict) else {}

    @property
    def motivation_output(self) -> dict[str, Any]:
        action = str(self._last_readout.get("action") or "")
        score = float(self._last_readout.get("score") or 0.0)
        direction = {"REST": "REST", "NO_ACTION": "FREEZE"}.get(action, "EXPLORE")
        return {
            "action_tendencies": ([{
                "direction": direction,
                "strength": score,
                "target": action,
                "cause_refs": ["contract-v1"],
            }] if action else [])
        }


class VirtualBrainMotivationEngine:
    """Maps VirtualBrain readouts onto MotivationState.

    PAD.P → valence; PAD.A → arousal; threat → threat;
    needs.pressure → drives; emotion → attention_bias; RPE → learning_signal.
    """

    def __init__(self, brain: VirtualBrainLike) -> None:
        self.brain = (
            ContractV1SessionBridge(brain)
            if callable(getattr(brain, "idle_tick", None))
            and callable(getattr(brain, "snapshot", None))
            and not callable(getattr(brain, "pad", None))
            else brain
        )

    @classmethod
    def wrap(cls, person: Any, *, provider: Any | None = None) -> VirtualBrainMotivationEngine:
        return cls(VirtualPersonBridge(person, provider))

    def process(
        self,
        *,
        self_model: SelfModel,
        viability: ViabilityAssessment,
        world_state: WorldState,
        event: Event | None,
        memory: MemoryView,
    ) -> MotivationState:
        ingest = getattr(self.brain, "ingest", None)
        if callable(ingest):
            ingest(event, world_state.timestamp)
        idle_tick = getattr(self.brain, "idle_tick", None)
        if callable(idle_tick):
            idle_tick(
                world_state={"environment": dict(world_state.environment)},
                event=event,
            )
        p, a, _d = self.brain.pad()
        threat = self.brain.threat_level()
        opportunity = self.brain.opportunity_level()
        drives = dict(self.brain.needs_pressures())
        emotion = self.brain.emotion_label()
        viability_tendencies, updates, v_drives, attention = tendencies_from_viability(
            viability, self_model
        )
        drives.update(v_drives)
        if emotion:
            attention[emotion] = max(attention.get(emotion, 0.0), 1.0)
        contract_tendencies = _contract_tendencies(self.brain)
        merged = list(contract_tendencies or viability_tendencies)
        if not merged:
            merged = [
                ActionTendency(
                    action="introspect",
                    strength=0.2,
                    reason=f"virtualbrain emotion={emotion}",
                )
            ]
        _ = memory
        research = getattr(self.brain, "motivation_state", None)
        return MotivationState(
            valence=p,
            arousal=a,
            urgency=max(threat, viability.urgency),
            threat=threat,
            opportunity=opportunity,
            drives=drives,
            attention_bias=attention,
            action_tendencies=sorted(merged, key=lambda x: x.strength, reverse=True),
            learning_signal=self.brain.rpe(),
            self_model_updates=updates
            + [
                SelfModelUpdate(
                    path="internal_state.affect",
                    new_value={"P": p, "A": a},
                    reason="virtualbrain pad mapping",
                    uncertainty=0.3,
                )
            ],
            attention_priority=list(getattr(research, "attention_priority", None) or attention.keys()),
            approach_avoid_bias=_float_or_default(
                getattr(research, "approach_avoid_bias", None), 0.0
            ),
            exploration_bias=_float_or_default(
                getattr(research, "exploration_bias", None), 0.5
            ),
            persistence=_float_or_default(getattr(research, "persistence", None), 0.5),
            risk_preference=_float_or_default(
                getattr(research, "risk_preference", None), 0.0
            ),
        )


def _contract_tendencies(brain: Any) -> list[ActionTendency]:
    payload = getattr(brain, "motivation_output", None)
    if callable(payload):
        payload = payload()
    if not isinstance(payload, dict):
        return []
    items = payload.get("action_tendencies") or []
    if not items:
        return []
    return action_tendencies_from_contract(list(items))


def map_from_readout(readout: dict[str, Any]) -> MotivationState:
    """Pure mapping helper for tests and adapters."""
    pad = readout.get("pad") or (0.0, 0.0, 0.0)
    return MotivationState(
        valence=float(pad[0]),
        arousal=float(pad[1]),
        urgency=float(readout.get("urgency") or 0.0),
        threat=float(readout.get("threat") or 0.0),
        opportunity=float(readout.get("opportunity") or 0.0),
        drives=dict(readout.get("pressures") or {}),
        attention_bias={str(readout.get("emotion") or "neutral"): 1.0},
        action_tendencies=[],
        learning_signal=float(readout.get("rpe") or 0.0),
        self_model_updates=[],
    )


def _float_or_default(value: Any, default: float) -> float:
    return default if value is None else float(value)
