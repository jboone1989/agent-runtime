from pathlib import Path

import pytest

from agent_runtime.capabilities import (
    CapabilityAccessMode,
    CapabilityBinding,
    CapabilityChallenger,
    CapabilityInventory,
    CapabilityKind,
    CapabilityLifecycleService,
    CapabilityLifecycleState,
    CapabilityRemovalDependencies,
    CapabilityReplacementEngine,
    CapabilityTrial,
    ReplaceableCapability,
    ReplacementStatus,
)


def spec(*, core=False):
    return ReplaceableCapability(
        capability_id="browser_automation",
        current_binding_id="playwright:v1",
        current_provider="playwright",
        current_version="1",
        core_protected=core,
        search_interval_cycles=10,
        minimum_trials=3,
        search_terms=("browser automation", "agent browser"),
    )


def trial(impl, bench, *, cycle=10, **overrides):
    values = {
        "success": True,
        "correctness": 0.90,
        "reliability": 0.90,
        "security": 0.95,
        "compatibility": 0.95,
        "maintenance": 0.70,
        "cost_efficiency": 0.60,
        "latency_efficiency": 0.60,
        "reversibility": 1.0,
    }
    values.update(overrides)
    return CapabilityTrial(
        capability_id="browser_automation",
        implementation_id=impl,
        benchmark_id=bench,
        cycle=cycle,
        evidence_refs=(f"trial:{impl}:{bench}",),
        **values,
    )


def seed_matched(engine, candidate="jev:v1", **candidate_overrides):
    for bench in ("login", "navigate", "extract"):
        engine.record_trial(trial("playwright:v1", bench))
        engine.record_trial(trial(candidate, bench, **candidate_overrides))


def test_core_subject_boundary_never_accepts_challengers(tmp_path):
    engine = CapabilityReplacementEngine(state_path=tmp_path / "state.json")
    engine.register(spec(core=True))
    assert engine.search_due("browser_automation", cycle=100, opportunity=True) is False
    with pytest.raises(ValueError, match="core-protected"):
        engine.discover(
            CapabilityChallenger(
                capability_id="browser_automation",
                candidate_id="jev:v1",
                provider="jev",
            )
        )


def test_search_is_periodic_but_resource_pressure_can_accelerate(tmp_path):
    engine = CapabilityReplacementEngine(state_path=tmp_path / "state.json")
    engine.register(spec())
    assert engine.search_due("browser_automation", cycle=1)
    engine.record_search("browser_automation", cycle=1, evidence_refs=("search:1",))
    assert not engine.search_due("browser_automation", cycle=5)
    assert engine.search_due("browser_automation", cycle=5, health=0.6)
    assert engine.search_due("browser_automation", cycle=5, opportunity=True)


def test_candidate_needs_matched_shadow_trials_before_promotion(tmp_path):
    engine = CapabilityReplacementEngine(state_path=tmp_path / "state.json")
    engine.register(spec())
    engine.discover(
        CapabilityChallenger(
            capability_id="browser_automation",
            candidate_id="jev:v1",
            provider="jev",
            discovered_cycle=2,
        )
    )
    engine.record_trial(trial("playwright:v1", "login"))
    engine.record_trial(trial("jev:v1", "login", latency_efficiency=0.9))

    decision = engine.decide("browser_automation", "jev:v1", cycle=3)
    assert decision.status is ReplacementStatus.INSUFFICIENT_EVIDENCE


def test_candidate_promotes_on_pareto_improvement_without_protected_regression(tmp_path):
    engine = CapabilityReplacementEngine(state_path=tmp_path / "state.json")
    engine.register(spec())
    engine.discover(
        CapabilityChallenger(
            capability_id="browser_automation",
            candidate_id="jev:v1",
            provider="jev",
            discovered_cycle=2,
        )
    )
    seed_matched(engine, latency_efficiency=0.90, cost_efficiency=0.80)

    decision = engine.decide("browser_automation", "jev:v1", cycle=20)
    assert decision.status is ReplacementStatus.PROMOTE
    assert "latency_efficiency" in decision.improved_dimensions
    assert "cost_efficiency" in decision.improved_dimensions


def test_security_or_compatibility_regression_blocks_better_speed(tmp_path):
    engine = CapabilityReplacementEngine(state_path=tmp_path / "state.json")
    engine.register(spec())
    engine.discover(
        CapabilityChallenger(
            capability_id="browser_automation",
            candidate_id="jev:v1",
            provider="jev",
        )
    )
    seed_matched(
        engine,
        latency_efficiency=0.99,
        security=0.81,
        compatibility=0.89,
    )

    decision = engine.decide("browser_automation", "jev:v1", cycle=20)
    assert decision.status is ReplacementStatus.REJECT
    assert "compatibility" in decision.reason


def test_promotion_keeps_rollback_path(tmp_path):
    inventory = CapabilityInventory()
    current = CapabilityBinding(
        capability_id="browser_automation",
        kind=CapabilityKind.LIBRARY,
        provider="playwright",
        binding_id="playwright:v1",
        candidate_id="playwright:v1",
        access_mode=CapabilityAccessMode.LOCAL,
        state=CapabilityLifecycleState.ACTIVE,
        version="1",
    )
    inventory.register(current)
    lifecycle = CapabilityLifecycleService(
        inventory,
        [],
        cycle_provider=lambda: 20,
        dependency_provider=lambda cid: CapabilityRemovalDependencies(
            capability_id=cid,
            binding_id=inventory.get(cid).binding_id if inventory.get(cid) else None,
            checked_at_cycle=20,
            revision="r1",
        ),
    )
    engine = CapabilityReplacementEngine(state_path=tmp_path / "state.json")
    engine.register(spec())
    engine.discover(
        CapabilityChallenger(
            capability_id="browser_automation",
            candidate_id="jev:v1",
            provider="jev",
        )
    )
    seed_matched(engine, latency_efficiency=0.90)
    decision = engine.decide("browser_automation", "jev:v1", cycle=20)
    replacement = CapabilityBinding(
        capability_id="browser_automation",
        kind=CapabilityKind.LIBRARY,
        provider="jev",
        binding_id="jev:v1",
        candidate_id="jev:v1",
        access_mode=CapabilityAccessMode.LOCAL,
        state=CapabilityLifecycleState.ACTIVE,
        version="1",
    )

    receipt = engine.promote(decision, replacement=replacement, lifecycle=lifecycle)
    assert inventory.get("browser_automation").provider == "jev"
    assert receipt.previous_binding["provider"] == "playwright"

    restored = engine.rollback("browser_automation", inventory=inventory)
    assert restored.provider == "playwright"
    assert inventory.get("browser_automation").binding_id == "playwright:v1"

    restarted = CapabilityReplacementEngine(state_path=tmp_path / "state.json")
    assert restarted.slots["browser_automation"].current_provider == "playwright"
