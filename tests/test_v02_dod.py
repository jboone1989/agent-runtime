"""Agent Runtime v0.2 — Sensory + Regulatory + Survival DoD."""
from __future__ import annotations

from datetime import datetime, timezone

from agent_runtime.clock import FrozenClock
from agent_runtime.governance.autonomy import AutonomyPolicy
from agent_runtime.regulation.state import RegulatoryState
from agent_runtime.runtime import AgentRuntime
from agent_runtime.sensing.signal import Signal
from agent_runtime.survival.failover import ProviderPool


def _rt(tmp_path, **kwargs) -> AgentRuntime:
    clock = kwargs.pop("clock", FrozenClock(datetime(2026, 8, 20, tzinfo=timezone.utc)))
    return AgentRuntime.bootstrap(state_dir=tmp_path, clock=clock, **kwargs)


def test_tick_without_observation_produces_records(tmp_path) -> None:
    rt = _rt(tmp_path)
    result = rt.tick()
    assert result.observation.observation_id
    assert rt.records.observations
    assert result.signals
    sources = {s.source for s in result.signals}
    assert len(sources) >= 4


def test_self_and_world_signal_namespaces(tmp_path) -> None:
    rt = _rt(tmp_path)
    rt.tick()
    scopes = {s.scope for s in rt.bus.recent()}
    assert "SELF" in scopes
    for s in rt.bus.recent():
        assert s.scope in {"SELF", "WORLD", "OTHER_AGENT", "UNKNOWN"}


def test_signals_do_not_directly_mutate_self_model(tmp_path) -> None:
    rt = _rt(tmp_path)
    before = dict(rt.self_model.uncertainty)
    signal = Signal.create(
        source="test",
        domain="test",
        kind="COMPUTE",
        value=0.01,
        severity=0.9,
        confidence=1.0,
        scope="SELF",
        timestamp=rt.clock.now(),
    )
    rt.bus.publish(signal)
    assert rt.self_model.uncertainty == before


def test_at_least_three_regulators(tmp_path) -> None:
    rt = _rt(tmp_path)
    names = {r.name for r in rt.coordinator.systems}
    assert {"service", "resource", "integrity"} <= names


def test_provider_failure_local_failover_without_user_prompt(tmp_path) -> None:
    pool = ProviderPool(active="primary", names=["primary", "backup"])
    rt = _rt(tmp_path, providers=pool)
    rt.sense_world.providers["primary"]["error_rate"] = 0.72
    rt.sense_world.providers["primary"]["available"] = True
    rt.sense_world.providers["backup"]["error_rate"] = 0.02
    result = rt.tick()
    assert pool.active == "backup"
    assert any(a.kind == "switch_provider" for a in result.local_actions)
    assert rt.records.regulations
    assert all(e.get("code") != "COGNITIVE_CAPABILITY_AT_RISK" for e in _esc_codes(result))


def test_all_providers_failed_escalates_to_viability_goal(tmp_path) -> None:
    pool = ProviderPool(active="primary", names=["primary", "backup"])
    rt = _rt(tmp_path, providers=pool)
    rt.sense_world.providers["primary"]["error_rate"] = 0.95
    rt.sense_world.providers["backup"]["error_rate"] = 0.95
    result = rt.tick()
    assert any(e.code == "COGNITIVE_CAPABILITY_AT_RISK" for e in result.escalations)
    assert any(e.evidence_refs for e in result.escalations)
    internals = [g for g in rt.goals if g.origin.value in {"VIABILITY", "INTERNAL"}]
    assert internals


def test_resource_shortage_enters_conservation(tmp_path) -> None:
    rt = _rt(tmp_path)
    rt.submit_goal("optional exploration")
    rt.sense_world.resources["compute"] = 0.12
    rt.sense_world.resources["token_budget"] = 0.08
    result = rt.tick()
    assert rt.regulatory_state.resource_conservation >= 0.6
    assert any(a.kind == "conserve" for a in result.local_actions)
    assert any(e.code == "RESOURCE_SURVIVAL_RISK" for e in result.escalations)


def test_integrity_contains_and_does_not_attack(tmp_path) -> None:
    rt = _rt(tmp_path)
    rt.sense_world.security["config_mutated"] = True
    rt.sense_world.security["credentials_valid"] = False
    result = rt.tick()
    kinds = {a.kind for a in result.local_actions}
    assert "contain" in kinds or "restore_config" in kinds or "disable_capability" in kinds
    assert not {"attack", "scan_external", "probe_source"} & kinds
    assert rt.self_model.capabilities["llm_provider"].available is True


def test_regulation_obeys_autonomy_policy(tmp_path) -> None:
    policy = AutonomyPolicy.default()
    policy.allowed_actions = {"noop", "introspect", "record", "diagnose", "maintain"}
    rt = _rt(tmp_path, autonomy=policy)
    rt.sense_world.providers["primary"]["error_rate"] = 0.8
    result = rt.tick()
    executed_local = {a.kind for a in result.local_actions}
    assert "switch_provider" not in executed_local
    assert result.regulation_denied


def test_regulatory_state_is_slow_and_persists(tmp_path) -> None:
    rt = _rt(tmp_path)
    before = RegulatoryState()
    assert before.stress == rt.regulatory_state.stress
    rt.sense_world.security["config_mutated"] = True
    rt.tick()
    after_first = rt.regulatory_state.defense_readiness
    assert after_first > before.defense_readiness
    assert after_first < 0.95
    rt.persist()
    restored = AgentRuntime.restore(state_dir=tmp_path)
    assert restored.regulatory_state.defense_readiness == after_first


def test_runtime_works_without_virtualbrain(tmp_path) -> None:
    rt = _rt(tmp_path)
    rt.tick()
    assert "virtualbrain" not in type(rt.motivation_engine).__module__


def test_run_loops_ticks(tmp_path) -> None:
    rt = _rt(tmp_path)
    results = rt.run(ticks=3)
    assert len(results) == 3
    assert rt.cycle == 3


def _esc_codes(result) -> list[dict]:
    return [{"code": e.code} for e in result.escalations]
