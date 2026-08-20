"""v0.2.1 freeze blockers — embodiment restore, evidence, gateway, mappers, contracts."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_runtime.body.boundary import BoundaryRelation, SelfBoundary
from agent_runtime.body.component import BodyComponent
from agent_runtime.body.model import BodyModel
from agent_runtime.clock import FrozenClock
from agent_runtime.embodiment import digital_pack, mock_physical_pack
from agent_runtime.environment.base import EnvironmentReader
from agent_runtime.perception.encoder import SignalEncoder
from agent_runtime.perception.percept import Percept
from agent_runtime.runtime import AgentRuntime, UnknownEmbodimentError
from agent_runtime.sensing.signal import Signal


def _clock() -> FrozenClock:
    return FrozenClock(datetime(2026, 8, 20, tzinfo=timezone.utc))


def test_mock_physical_restore_keeps_physical_embodiment(tmp_path) -> None:
    rt = AgentRuntime.bootstrap(
        tmp_path, clock=_clock(), embodiment=mock_physical_pack()
    )
    assert rt.embodiment_descriptor.embodiment_type == "mock_physical"
    rt.persist()
    restored = AgentRuntime.restore(tmp_path, clock=_clock())
    assert restored.embodiment_descriptor.embodiment_type == "mock_physical"
    assert restored.environment.environment_id == "mock_physical"


def test_restore_requires_resolver_for_unknown_embodiment(tmp_path) -> None:
    rt = AgentRuntime.bootstrap(tmp_path, clock=_clock())
    rt.embodiment_descriptor.embodiment_type = "robot-x"
    rt.persist()
    with pytest.raises(UnknownEmbodimentError):
        AgentRuntime.restore(tmp_path, clock=_clock())


def test_physical_state_is_reobserved_not_replayed(tmp_path) -> None:
    pack = mock_physical_pack()
    pack.environment.apply("battery", 0.12)
    rt = AgentRuntime.bootstrap(tmp_path, clock=_clock(), embodiment=pack)
    rt.tick()
    rt.persist()
    restored = AgentRuntime.restore(tmp_path, clock=_clock())
    assert float(restored.environment.read("battery")) > 0.5


def test_digital_persistent_body_state_can_restore(tmp_path) -> None:
    rt = AgentRuntime.bootstrap(tmp_path, clock=_clock(), embodiment=digital_pack())
    ids = set(rt.self_model.body.components)
    rt.persist()
    restored = AgentRuntime.restore(tmp_path, clock=_clock())
    assert set(restored.self_model.body.components) == ids
    assert restored.identity.agent_id == rt.identity.agent_id
    assert restored.embodiment_descriptor.embodiment_type == "digital"


def test_signal_lineage_reaches_self_model_update(tmp_path) -> None:
    rt = AgentRuntime.bootstrap(tmp_path, clock=_clock())
    rt.environment.apply("resources/disk", 0.08)
    rt.tick()
    belief = rt.self_model.internal_state.get("storage")
    assert belief is not None
    assert any(ref.startswith("raw_") or ref.startswith("sig_") or ref.startswith("obs_") for ref in belief.evidence_refs)
    assert any(ref.startswith("obs_") for ref in belief.evidence_refs)
    assert len(belief.evidence_refs) >= 2


def test_signal_lineage_reaches_internal_goal(tmp_path) -> None:
    rt = AgentRuntime.bootstrap(tmp_path, clock=_clock())
    rt.environment.apply("resources/disk", 0.08)
    rt.tick()
    internals = [g for g in rt.goals if g.origin.value in {"VIABILITY", "INTERNAL"}]
    assert internals
    evidence = internals[0].evidence_refs
    assert evidence
    assert any(ref.startswith("obs_") or ref.startswith("sig_") or ref.startswith("esc_") for ref in evidence)


def test_planner_action_uses_actuator_gateway(tmp_path) -> None:
    rt = AgentRuntime.bootstrap(tmp_path, clock=_clock())
    rt.submit_goal("do a noop")
    result = rt.tick()
    assert rt.actuation_gateway is not None
    kinds = {a.kind for a in result.actions}
    if kinds:
        assert rt.actuation_gateway.last_route is not None
        assert "executor.execute" not in (rt.actuation_gateway.last_route or "")


def test_regulatory_action_uses_same_actuator_gateway(tmp_path) -> None:
    rt = AgentRuntime.bootstrap(tmp_path, clock=_clock())
    rt.environment.apply("providers/primary/error_rate", 0.72)
    rt.environment.apply("providers/backup/error_rate", 0.02)
    result = rt.tick()
    assert any(a.kind == "switch_provider" for a in result.local_actions)
    assert rt.actuation_gateway.last_route == "actuator_registry"


def test_sensor_cannot_mutate_environment_by_interface(tmp_path) -> None:
    rt = AgentRuntime.bootstrap(tmp_path, clock=_clock())
    ctx_env = rt._sensor_environment()
    assert isinstance(ctx_env, EnvironmentReader)
    assert not hasattr(ctx_env, "apply")


def test_new_signal_mapper_can_be_registered_without_editing_core_encoder() -> None:
    encoder = SignalEncoder()
    now = datetime(2026, 8, 20, tzinfo=timezone.utc)

    class ExtraMapper:
        def accepts(self, percept: Percept) -> bool:
            return percept.kind == "VISION_HAZARD"

        def encode(self, percept: Percept) -> list[Signal]:
            return [
                Signal.create(
                    source="vision",
                    domain="world",
                    kind="HAZARD",
                    value=percept.attributes.get("level", 1.0),
                    severity=0.9,
                    confidence=0.8,
                    scope="WORLD",
                    timestamp=percept.timestamp,
                    evidence_refs=list(percept.source_observation_ids) + [percept.percept_id],
                )
            ]

    encoder.register(ExtraMapper())
    percept = Percept.from_raw(
        observation_id="raw_x",
        modality="vision",
        kind="VISION_HAZARD",
        timestamp=now,
        attributes={"level": 0.7},
    )
    signals = encoder.encode([percept])
    assert signals and signals[0].kind == "HAZARD"


def test_body_boundary_registration_is_idempotent() -> None:
    body = BodyModel()
    component = BodyComponent("memory_store", "memory", {"remember"}, 1.0)
    body.register(component, SelfBoundary("memory_store", BoundaryRelation.BODY, 0.9))
    body.register(component, SelfBoundary("memory_store", BoundaryRelation.OWNED, 0.8))
    assert len(body.boundaries) == 1
    assert body.relation_of("memory_store") == BoundaryRelation.OWNED


def test_contract_v1_golden_fixture() -> None:
    root = Path(__file__).resolve().parents[1] / "contracts" / "v1"
    fixture = root / "fixtures" / "motivation_output.json"
    assert fixture.exists()
    import json

    payload = json.loads(fixture.read_text(encoding="utf-8"))
    assert payload["contract_version"] == "1.0"
    assert "action_tendencies" in payload
    assert "tendencies" not in payload
    body = json.loads((root / "fixtures" / "body_state_view.json").read_text(encoding="utf-8"))
    assert "components" in body
    assert "sensed_channels" in body
    assert "inferred_channels" in body
    assert "extensions" in body
    from agent_runtime.integrations.canonical import CONTRACT_VERSION, motivation_output
    from agent_runtime.motivation.engine import MotivationState

    payload_rt = motivation_output(MotivationState.neutral())
    assert payload_rt["contract_version"] == CONTRACT_VERSION
    assert "action_tendencies" in payload_rt
    assert "tendencies" not in payload_rt
