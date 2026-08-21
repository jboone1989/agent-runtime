"""ContractMotivationEngine: Contract v1 in/out, interpreter, no viability merge."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from referencing import Registry, Resource

from agent_runtime.cognition.world_model import WorldState
from agent_runtime.clock import SystemClock
from agent_runtime.identity import create_genesis_identity
from agent_runtime.memory.view import MemoryView
from agent_runtime.motivation.port import InProcessMotivationPort
from agent_runtime.self_model.model import SelfModel
from agent_runtime.viability.state import Threat, ViabilityAssessment


def _valid_output(**overrides: Any) -> dict:
    payload = {
        "contract_version": "1.0",
        "valence": 0.1,
        "arousal": 0.4,
        "urgency": 0.3,
        "threat": 0.2,
        "opportunity": 0.1,
        "drives": {"safety": 0.4},
        "attention_priority": ["safety"],
        "action_tendencies": [
            {"direction": "AVOID", "strength": 0.6, "target": None, "cause_refs": ["sig_1"]}
        ],
        "approach_avoid_bias": -0.3,
        "exploration_bias": 0.5,
        "persistence": 0.5,
        "risk_preference": 0.3,
        "learning_signal": 0.0,
        "self_model_updates": [
            {
                "path": "internal_state.note",
                "new_value": "x",
                "reason": "mind",
                "uncertainty": 0.2,
                "evidence_refs": ["e1"],
            }
        ],
    }
    payload.update(overrides)
    return payload


class _FixedPort:
    def __init__(self, response: dict | None = None, *, error: Exception | None = None) -> None:
        self.response = response if response is not None else _valid_output()
        self.error = error
        self.requests: list[dict] = []

    def evaluate(self, request: dict) -> dict:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.response


def _process(port, **engine_kwargs):
    from agent_runtime.motivation.contract_engine import ContractMotivationEngine

    ident = create_genesis_identity("0.2.2", SystemClock())
    model = SelfModel.bootstrap(ident, ident.created_at)
    engine = ContractMotivationEngine(
        InProcessMotivationPort(port),
        agent_id=ident.agent_id,
        **engine_kwargs,
    )
    viability = ViabilityAssessment(
        score=0.4,
        threats=[Threat("compute", 0.9, "provider failing", True)],
        opportunities=[],
        urgency=0.8,
        uncertainty=0.2,
    )
    return engine.process(
        self_model=model,
        viability=viability,
        world_state=WorldState(timestamp=datetime.now(timezone.utc)),
        event=None,
        memory=MemoryView(0, [], {}),
    ), ident, viability, model


def test_built_motivation_input_is_schema_valid() -> None:
    port = _FixedPort()
    _process(port)
    request = port.requests[0]
    root = Path(__file__).resolve().parents[1] / "contracts" / "v1"
    registry = Registry()
    for path in (root / "schemas").glob("*.schema.json"):
        schema = json.loads(path.read_text(encoding="utf-8"))
        registry = registry.with_resource(path.name, Resource.from_contents(schema))
    schema = json.loads((root / "schemas" / "motivation_input.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema, registry=registry).validate(request)


def test_schema_invalid_output_missing_fields_raises() -> None:
    bad = _valid_output()
    del bad["valence"]
    with pytest.raises(jsonschema.ValidationError):
        _process(_FixedPort(bad))


def test_unknown_direction_raises_without_neutral_fallback() -> None:
    bad = _valid_output(
        action_tendencies=[{"direction": "FLEE", "strength": 0.9, "target": None, "cause_refs": []}]
    )
    with pytest.raises(jsonschema.ValidationError):
        _process(_FixedPort(bad))


def test_avoid_becomes_conserve_and_lowers_risk_and_exploration() -> None:
    state, *_ = _process(_FixedPort(_valid_output(exploration_bias=0.5, risk_preference=0.3)))
    assert state.action_tendencies[0].action == "conserve"
    assert state.exploration_bias < 0.5
    assert state.risk_preference < 0.3
    assert state.valence == 0.1
    assert state.attention_priority == ["safety"]
    assert state.self_model_updates[0].path == "internal_state.note"
    assert state.self_model_updates[0].evidence_refs == ["e1"]


def test_explore_becomes_introspect() -> None:
    payload = _valid_output(
        action_tendencies=[{"direction": "EXPLORE", "strength": 0.7, "target": None, "cause_refs": []}]
    )
    state, *_ = _process(_FixedPort(payload))
    assert state.action_tendencies[0].action == "introspect"


def test_engine_does_not_merge_tendencies_from_viability(monkeypatch) -> None:
    def boom(*_args, **_kwargs):
        raise AssertionError("ContractMotivationEngine must not call tendencies_from_viability")

    monkeypatch.setattr(
        "agent_runtime.motivation.tendencies.tendencies_from_viability", boom
    )
    import agent_runtime.motivation.contract_engine as mod

    if hasattr(mod, "tendencies_from_viability"):
        monkeypatch.setattr(mod, "tendencies_from_viability", boom)

    payload = _valid_output(
        action_tendencies=[{"direction": "EXPLORE", "strength": 0.4, "target": None, "cause_refs": []}]
    )
    state, *_ = _process(_FixedPort(payload))
    assert {t.action for t in state.action_tendencies} == {"introspect"}


def test_port_exception_propagates() -> None:
    with pytest.raises(RuntimeError, match="mind down"):
        _process(_FixedPort(error=RuntimeError("mind down")))


def test_invalid_built_input_raises(monkeypatch) -> None:
    from agent_runtime.integrations import canonical

    monkeypatch.setattr(canonical, "motivation_input", lambda **_kwargs: {"contract_version": "1.0"})
    with pytest.raises(jsonschema.ValidationError):
        _process(_FixedPort())


def test_interpreter_receives_empty_goals_and_capabilities() -> None:
    seen: dict[str, Any] = {}

    class _Spy:
        def interpret(self, tendency, *, self_model, world_state, goals, available_capabilities):
            seen["goals"] = goals
            seen["available_capabilities"] = available_capabilities
            from agent_runtime.motivation.interpreter import DefaultTendencyInterpreter

            return DefaultTendencyInterpreter().interpret(
                tendency,
                self_model=self_model,
                world_state=world_state,
                goals=goals,
                available_capabilities=available_capabilities,
            )

    _process(_FixedPort(), interpreter=_Spy())
    assert seen["goals"] == []
    assert seen["available_capabilities"] == {}


def test_unknown_critical_extension_on_input_raises(monkeypatch) -> None:
    from agent_runtime.contracts.extensions import ExtensionValidationError
    from agent_runtime.integrations import canonical

    real = canonical.motivation_input

    def wrapped(**kwargs):
        req = real(**kwargs)
        req["self_state"]["extensions"] = {
            "acme.probe": {"critical": True, "version": "1", "payload": {}}
        }
        return req

    monkeypatch.setattr(canonical, "motivation_input", wrapped)
    with pytest.raises(ExtensionValidationError, match="critical"):
        _process(_FixedPort())


def test_engine_source_has_no_virtualbrain_or_viability_tendencies() -> None:
    import inspect

    from agent_runtime.motivation import contract_engine

    src = inspect.getsource(contract_engine)
    assert "import virtualbrain" not in src
    assert "from virtualbrain" not in src
    assert "tendencies_from_viability" not in src
