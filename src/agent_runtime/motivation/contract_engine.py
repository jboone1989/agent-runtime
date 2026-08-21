"""Contract-driven MotivationEngine. Viability is input; the mind responds."""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
from referencing import Registry, Resource

from ..cognition.world_model import WorldState
from ..integrations import canonical
from ..integrations.canonical import ContractActionTendency
from ..memory.view import MemoryView
from ..observations import Event
from ..self_model.model import SelfModel
from ..viability.state import ViabilityAssessment
from .engine import ActionTendency, MotivationState, SelfModelUpdate
from .interpreter import DefaultTendencyInterpreter, TendencyInterpreter
from .port import MotivationPort

_INPUT_SCHEMA = "motivation_input.schema.json"
_OUTPUT_SCHEMA = "motivation_output.schema.json"


def _schema_root() -> Path:
    start = Path(__file__).resolve()
    for parent in [start, *start.parents]:
        candidate = parent / "contracts" / "v1" / "schemas"
        if (candidate / _INPUT_SCHEMA).is_file():
            return candidate
    raise FileNotFoundError("contracts/v1/schemas not found")


def _validator(schema_name: str) -> jsonschema.Draft202012Validator:
    root = _schema_root()
    registry = Registry()
    for path in root.glob("*.schema.json"):
        schema = json.loads(path.read_text(encoding="utf-8"))
        resource = Resource.from_contents(schema)
        registry = registry.with_resource(path.name, resource)
        if "$id" in schema:
            registry = registry.with_resource(schema["$id"], resource)
    schema = json.loads((root / schema_name).read_text(encoding="utf-8"))
    return jsonschema.Draft202012Validator(schema, registry=registry)


def _clip(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


class ContractMotivationEngine:
    def __init__(
        self,
        port: MotivationPort,
        interpreter: TendencyInterpreter | None = None,
        agent_id: str = "",
    ) -> None:
        self._port = port
        self._interpreter = interpreter or DefaultTendencyInterpreter()
        self._agent_id = agent_id
        self._input_validator = _validator(_INPUT_SCHEMA)
        self._output_validator = _validator(_OUTPUT_SCHEMA)

    def process(
        self,
        *,
        self_model: SelfModel,
        viability: ViabilityAssessment,
        world_state: WorldState,
        event: Event | None,
        memory: MemoryView,
    ) -> MotivationState:
        _ = memory
        request = canonical.motivation_input(
            self_model=self_model,
            agent_id=self._agent_id,
            world_state=world_state,
            viability=viability,
            event=event,
        )
        self._input_validator.validate(request)
        response = self._port.evaluate(request)
        self._output_validator.validate(response)

        exploration = float(response["exploration_bias"])
        risk = float(response["risk_preference"])
        merged: dict[str, ActionTendency] = {}
        for item in response.get("action_tendencies") or []:
            tendency = ContractActionTendency(
                direction=item["direction"],
                strength=float(item.get("strength") or 0.0),
                target=item.get("target"),
                cause_refs=list(item.get("cause_refs") or []),
            )
            effect = self._interpreter.interpret(
                tendency,
                self_model=self_model,
                world_state=world_state,
                goals=[],
                available_capabilities={},
            )
            exploration += effect.exploration_delta
            risk += effect.risk_bias
            for executable in effect.executable_tendencies:
                prev = merged.get(executable.action)
                if prev is None or executable.strength > prev.strength:
                    merged[executable.action] = executable

        updates = [
            SelfModelUpdate(
                path=str(item["path"]),
                new_value=item.get("new_value"),
                reason=str(item["reason"]),
                uncertainty=float(item.get("uncertainty") or 0.0),
                evidence_refs=list(item.get("evidence_refs") or []),
            )
            for item in response.get("self_model_updates") or []
        ]
        return MotivationState(
            valence=_clip(response["valence"], -1.0, 1.0),
            arousal=_clip(response["arousal"], -1.0, 1.0),
            urgency=_clip(response["urgency"], 0.0, 1.0),
            threat=_clip(response["threat"], 0.0, 1.0),
            opportunity=_clip(response["opportunity"], 0.0, 1.0),
            drives={str(k): float(v) for k, v in dict(response.get("drives") or {}).items()},
            attention_bias={},
            action_tendencies=sorted(merged.values(), key=lambda t: t.strength, reverse=True),
            learning_signal=_clip(response["learning_signal"], -1.0, 1.0),
            self_model_updates=updates,
            attention_priority=list(response.get("attention_priority") or []),
            approach_avoid_bias=_clip(response["approach_avoid_bias"], -1.0, 1.0),
            exploration_bias=_clip(exploration, 0.0, 1.0),
            persistence=_clip(response["persistence"], 0.0, 1.0),
            risk_preference=_clip(risk, -1.0, 1.0),
        )
