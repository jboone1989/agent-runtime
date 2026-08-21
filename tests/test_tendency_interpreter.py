"""DefaultTendencyInterpreter: contract direction → TendencyEffect (SimplePlanner compat)."""
from __future__ import annotations

from datetime import datetime, timezone

from agent_runtime.cognition.world_model import WorldState
from agent_runtime.clock import SystemClock
from agent_runtime.identity import create_genesis_identity
from agent_runtime.integrations.canonical import ContractActionTendency
from agent_runtime.motivation.interpreter import DefaultTendencyInterpreter
from agent_runtime.self_model.model import SelfModel


def _ctx() -> tuple[SelfModel, WorldState]:
    ident = create_genesis_identity("0.2.2", SystemClock())
    model = SelfModel.bootstrap(ident, ident.created_at)
    world = WorldState(timestamp=datetime.now(timezone.utc))
    return model, world


def _interpret(direction: str, strength: float = 0.8):
    model, world = _ctx()
    interpreter = DefaultTendencyInterpreter()
    return interpreter.interpret(
        ContractActionTendency(direction=direction, strength=strength),  # type: ignore[arg-type]
        self_model=model,
        world_state=world,
        goals=[],
        available_capabilities={},
    )


def test_avoid_maps_to_conserve_and_lowers_risk_and_exploration() -> None:
    effect = _interpret("AVOID")
    assert effect.executable_tendencies
    assert effect.executable_tendencies[0].action == "conserve"
    assert effect.risk_bias < 0
    assert effect.exploration_delta < 0


def test_rest_maps_to_conserve_and_lowers_risk_and_exploration() -> None:
    effect = _interpret("REST")
    assert effect.executable_tendencies[0].action == "conserve"
    assert effect.risk_bias < 0
    assert effect.exploration_delta < 0


def test_explore_maps_to_introspect_and_raises_exploration() -> None:
    effect = _interpret("EXPLORE")
    assert effect.executable_tendencies[0].action == "introspect"
    assert effect.exploration_delta > 0


def test_confront_maps_to_diagnose() -> None:
    effect = _interpret("CONFRONT")
    assert effect.executable_tendencies[0].action == "diagnose"


def test_seek_support_maps_to_record_temporary_compat_only() -> None:
    effect = _interpret("SEEK_SUPPORT")
    assert effect.executable_tendencies[0].action == "record"


def test_approach_maps_to_maintain() -> None:
    effect = _interpret("APPROACH")
    assert effect.executable_tendencies[0].action == "maintain"


def test_freeze_maps_to_noop_and_lowers_exploration() -> None:
    effect = _interpret("FREEZE")
    assert effect.executable_tendencies[0].action == "noop"
    assert effect.exploration_delta < 0
