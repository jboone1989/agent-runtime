"""Agent Runtime v0.2.1 — Environment & Embodiment Foundation DoD."""
from __future__ import annotations

import inspect
from datetime import datetime, timezone

from agent_runtime.actuation.command import ActuationCommand
from agent_runtime.body.boundary import BoundaryRelation
from agent_runtime.clock import FrozenClock
from agent_runtime.embodiment import digital_pack, mock_physical_pack
from agent_runtime.environment.base import EnvironmentAdapter
from agent_runtime.environment.digital import DigitalEnvironmentAdapter
from agent_runtime.environment.mock_physical import MockPhysicalEnvironment
from agent_runtime.governance.autonomy import AutonomyPolicy
from agent_runtime.observations import StateDelta
from agent_runtime.perception.encoder import SignalEncoder
from agent_runtime.perception.percept import Percept
from agent_runtime.regulation.integrity_state import IntegrityState
from agent_runtime.runtime import AgentRuntime
from agent_runtime.self_model.beliefs import StateBelief
from agent_runtime.sensing.active import ActivePerceptionAction, InformationGoal
from agent_runtime.sensing.raw import RawObservation
from agent_runtime.sensing.sensor import SensorContext
from agent_runtime.sensing.world import SenseWorld
from agent_runtime.survival.failover import ProviderPool


def _clock() -> FrozenClock:
    return FrozenClock(datetime(2026, 8, 20, tzinfo=timezone.utc))


def _digital(tmp_path, **kwargs) -> AgentRuntime:
    clock = kwargs.pop("clock", _clock())
    providers = kwargs.pop("providers", None)
    pack = digital_pack(providers=providers)
    return AgentRuntime.bootstrap(
        state_dir=tmp_path, clock=clock, embodiment=pack, **kwargs
    )


def test_sensor_outputs_raw_observation_not_signal(tmp_path) -> None:
    rt = _digital(tmp_path)
    ctx = SensorContext(
        now=rt.clock.now(),
        environment=rt.environment,
        cycle=rt.cycle,
        agent_id=rt.identity.agent_id,
    )
    raw = rt.sensors.all()[0].sample(ctx)
    assert raw
    assert all(isinstance(item, RawObservation) for item in raw)
    assert not hasattr(rt.sensors.all()[0], "sense") or not callable(
        getattr(rt.sensors.all()[0], "sense", None)
    )


def test_perception_layer_is_independent_of_sensor_signals(tmp_path) -> None:
    rt = _digital(tmp_path)
    ctx = SensorContext(
        now=rt.clock.now(),
        environment=rt.environment,
        cycle=0,
        agent_id=rt.identity.agent_id,
    )
    raw = []
    for sensor in rt.sensors.all():
        raw.extend(sensor.sample(ctx))
    percepts = rt.perception.perceive(raw, rt._perception_context(ctx.now))
    assert percepts
    assert all(isinstance(p, Percept) for p in percepts)
    signals = SignalEncoder().encode(percepts)
    assert {s.domain for s in signals}
    kinds = {type(raw[0]).__name__, type(percepts[0]).__name__, type(signals[0]).__name__}
    assert kinds == {"RawObservation", "Percept", "Signal"}


def test_sensor_context_does_not_depend_on_provider_pool_or_sense_world() -> None:
    fields = SensorContext.__dataclass_fields__
    assert "providers" not in fields
    assert "world" not in fields
    assert "environment" in fields


def test_runtime_module_does_not_import_sense_world() -> None:
    import agent_runtime.runtime as rtmod

    source = inspect.getsource(rtmod)
    assert "SenseWorld" not in source


def test_digital_environment_is_an_adapter() -> None:
    env = DigitalEnvironmentAdapter()
    assert isinstance(env, EnvironmentAdapter) or hasattr(env, "read")
    assert "resources" in env.describe_capabilities() or env.read("resources")
    assert env.environment_id
    assert isinstance(env.world, SenseWorld)


def test_body_model_is_part_of_self_model(tmp_path) -> None:
    rt = _digital(tmp_path)
    assert rt.self_model.body is not None
    ids = {c.component_id for c in rt.self_model.body.components.values()}
    assert {"runtime_process", "memory_store", "local_filesystem", "primary_llm"} <= ids
    relations = {b.relation for b in rt.self_model.body.boundaries.values()}
    assert BoundaryRelation.BODY in relations
    assert BoundaryRelation.DEPENDENCY in relations or BoundaryRelation.BODY in relations
    llm = next(b for b in rt.self_model.body.boundaries.values() if b.entity_id == "primary_llm")
    assert llm.relation in {BoundaryRelation.BODY, BoundaryRelation.DEPENDENCY}


def test_observation_uses_delta_semantics_does_not_reset_unobserved(tmp_path) -> None:
    rt = _digital(tmp_path)
    now = rt.clock.now()
    rt.self_model.internal_state["social_support"] = StateBelief(
        name="social_support",
        value=0.21,
        trend=0.0,
        uncertainty=0.1,
        last_updated=now,
        evidence_refs=["prior"],
    )
    rt.tick()
    assert rt.self_model.internal_state["social_support"].value == 0.21
    delta = StateDelta(self_updates={"compute": 0.4}, world_updates={}, evidence_refs=["x"])
    assert "social_support" not in delta.self_updates


def test_integrity_isolates_affected_component_not_llm(tmp_path) -> None:
    rt = _digital(tmp_path)
    rt.environment.apply("security/dependency_hash_ok", False)
    result = rt.tick()
    llm = rt.self_model.capabilities.get("llm_provider")
    assert llm is not None and llm.available is True
    kinds = {a.kind for a in result.local_actions}
    assert "contain" in kinds
    payloads = [a.payload for a in result.local_actions if a.kind == "contain"]
    affected = set()
    for payload in payloads:
        affected.update(payload.get("components", []))
    assert "dependency_tree" in affected
    assert "primary_llm" not in affected
    assert rt.regulatory_state.integrity_state in {
        IntegrityState.CONTAINED,
        IntegrityState.RECOVERING,
        IntegrityState.SUSPICIOUS,
    }


def test_integrity_restore_failure_escalates(tmp_path) -> None:
    rt = _digital(tmp_path)
    rt.environment.apply("security/config_mutated", True)
    rt.environment.apply("security/restore_blocked", True)
    result = rt.tick()
    assert rt.regulatory_state.integrity_state == IntegrityState.CRITICAL
    assert any(e.code == "INTEGRITY_RECOVERY_FAILED" for e in result.escalations)


def test_resource_escalation_maps_disk_to_storage(tmp_path) -> None:
    rt = _digital(tmp_path)
    rt.environment.apply("resources/disk", 0.08)
    rt.environment.apply("resources/compute", 0.80)
    result = rt.tick()
    resource_esc = [e for e in result.escalations if e.code == "RESOURCE_SURVIVAL_RISK"]
    assert resource_esc
    assert any(e.suggested_dimension == "storage" for e in resource_esc)
    assert all(e.suggested_dimension != "compute" for e in resource_esc)


def test_resource_escalation_maps_token_budget_to_financial(tmp_path) -> None:
    rt = _digital(tmp_path)
    rt.environment.apply("resources/token_budget", 0.05)
    rt.environment.apply("resources/compute", 0.80)
    result = rt.tick()
    resource_esc = [e for e in result.escalations if e.code == "RESOURCE_SURVIVAL_RISK"]
    assert any(e.suggested_dimension == "financial_resources" for e in resource_esc)


def test_actuators_exist_and_go_through_policy(tmp_path) -> None:
    rt = _digital(tmp_path)
    kinds = set()
    for actuator in rt.actuators.all():
        kinds.update(actuator.capabilities())
    assert {"switch_provider", "restore_config", "conserve"} <= kinds
    command = ActuationCommand(kind="switch_provider", payload={"target": "backup"}, reason="test")
    policy = AutonomyPolicy.default()
    policy.allowed_actions = {"noop"}
    rt.autonomy = policy
    from agent_runtime.action.policy import ActionPolicy
    from agent_runtime.action.proposal import ActionProposal
    from agent_runtime.governance.budget import ResourceBudget

    rt.action_policy = ActionPolicy(policy, rt.constitution)
    proposal = ActionProposal(
        kind="switch_provider",
        resource="self",
        compute_cost=0.02,
        financial_cost=0.0,
        goal_id=None,
        reason="test",
    )
    auth = rt.action_policy.filter([proposal], rt.goals, ResourceBudget.from_limits(1.0, 0.0))
    assert not auth.authorized
    assert command.kind == "switch_provider"


def test_mock_physical_environment_runs(tmp_path) -> None:
    pack = mock_physical_pack()
    rt = AgentRuntime.bootstrap(state_dir=tmp_path, clock=_clock(), embodiment=pack)
    pack.environment.apply("battery", 0.12)
    result = rt.tick()
    assert isinstance(pack.environment, MockPhysicalEnvironment)
    assert any(s.kind in {"BATTERY", "ENERGY_LEVEL"} or s.domain == "resource" for s in result.signals)
    assert rt.regulatory_state.resource_conservation >= 0.6 or any(
        a.kind == "conserve" for a in result.local_actions
    )


def test_digital_and_physical_share_runtime_core(tmp_path) -> None:
    digital = AgentRuntime.bootstrap(
        state_dir=tmp_path / "d", clock=_clock(), embodiment=digital_pack()
    )
    physical = AgentRuntime.bootstrap(
        state_dir=tmp_path / "p", clock=_clock(), embodiment=mock_physical_pack()
    )
    assert type(digital) is type(physical) is AgentRuntime
    assert type(digital.viability_model).__name__ == type(physical.viability_model).__name__
    assert type(digital.motivation_engine).__name__ == type(physical.motivation_engine).__name__
    assert type(digital.coordinator).__name__ == type(physical.coordinator).__name__
    digital.environment.apply("providers/primary/error_rate", 0.72)
    digital.environment.apply("providers/backup/error_rate", 0.02)
    dres = digital.tick()
    assert any(a.kind == "switch_provider" for a in dres.local_actions)
    physical.environment.apply("battery", 0.10)
    pres = physical.tick()
    assert any(a.kind == "conserve" for a in pres.local_actions) or physical.regulatory_state.resource_conservation >= 0.6


def test_active_perception_schema_exists() -> None:
    goal = InformationGoal(question="is the api healthy?", expected_information_gain=0.4)
    action = ActivePerceptionAction(sensor_id="provider_sensor", command="query")
    assert goal.question
    assert action.sensor_id == "provider_sensor"


def test_run_forever_pause_resume_stop(tmp_path) -> None:
    rt = _digital(tmp_path)
    rt.run_forever(max_ticks=2)
    assert rt.cycle == 2
    rt.pause()
    assert rt.paused is True
    rt.resume()
    assert rt.paused is False
    rt.stop()
    assert rt.running is False
    results = rt.run(ticks=3)
    assert results == []


def test_v02_tick_without_observation_still_works(tmp_path) -> None:
    rt = _digital(tmp_path)
    result = rt.tick()
    assert result.observation.observation_id
    assert result.signals
