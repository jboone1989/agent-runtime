"""Bounded usage learning and governed capability retirement."""
from __future__ import annotations

import pytest

from agent_runtime.capabilities import (
    CapabilityAccessMode,
    CapabilityBinding,
    CapabilityKind,
    CapabilityLifecycleService,
    CapabilityLifecycleState,
    CapabilityUsageRecord,
)
from agent_runtime.capabilities.inventory import CapabilityInventory
from agent_runtime.runtime import AgentRuntime


def _binding(provider: str = "fixture", *, state=CapabilityLifecycleState.ACTIVE):
    return CapabilityBinding(
        capability_id="research.web",
        kind=CapabilityKind.TOOL,
        provider=provider,
        access_mode=CapabilityAccessMode.LOCAL,
        state=state,
        binding_id=f"binding:{provider}",
        candidate_id=f"candidate:{provider}",
    )


def _use(cycle: int, success: bool, *, evidence_result="COVERED"):
    return CapabilityUsageRecord(
        capability_id="research.web",
        cycle=cycle,
        binding_id="binding:fixture",
        candidate_id="candidate:fixture",
        success=success,
        evidence_result=evidence_result,
        evidence_refs=[f"out:{cycle}"],
    )


def test_usage_updates_health_and_degrades_after_bounded_failures() -> None:
    inventory = CapabilityInventory(entries={"research.web": _binding()})
    usage: list[CapabilityUsageRecord] = []
    lifecycle = CapabilityLifecycleService(inventory, usage)

    lifecycle.record_use(_use(1, False))
    lifecycle.record_use(_use(2, False))
    assert inventory.get("research.web").state == CapabilityLifecycleState.ACTIVE
    lifecycle.record_use(_use(3, False))

    binding = inventory.get("research.web")
    assert binding.state == CapabilityLifecycleState.DEGRADED
    assert binding.health < 1.0
    assert len(usage) == 3


def test_replacement_preserves_capability_identity_and_history() -> None:
    inventory = CapabilityInventory(entries={"research.web": _binding()})
    usage = [_use(1, True)]
    lifecycle = CapabilityLifecycleService(inventory, usage)

    history = lifecycle.replace(
        "research.web",
        _binding("replacement"),
        cycle=2,
        reason="new candidate is more reliable",
        evidence_refs=["decision:2"],
    )
    assert history.replacement_reference == "binding:replacement"
    assert inventory.get("research.web").provider == "replacement"
    assert inventory.history[0].evidence_refs == ["out:1", "decision:2"]

    with pytest.raises(ValueError, match="binding_id must be unique"):
        lifecycle.replace(
            "research.web",
            _binding("fixture"),
            cycle=3,
            reason="reused binding",
        )

    with pytest.raises(ValueError, match="unique binding_id"):
        lifecycle.replace(
            "research.web",
            CapabilityBinding(
                capability_id="research.web",
                kind=CapabilityKind.TOOL,
                provider="missing-id",
            ),
            cycle=3,
            reason="missing binding",
        )

    with pytest.raises(ValueError, match="preserve capability identity"):
        lifecycle.replace(
            "research.web",
            CapabilityBinding(
                capability_id="different",
                kind=CapabilityKind.TOOL,
                provider="bad",
            ),
            cycle=3,
            reason="invalid",
        )


def test_replacement_does_not_inherit_old_binding_failures() -> None:
    inventory = CapabilityInventory(entries={"research.web": _binding()})
    usage: list[CapabilityUsageRecord] = []
    lifecycle = CapabilityLifecycleService(inventory, usage)
    for cycle in range(1, 4):
        lifecycle.record_use(_use(cycle, False))

    lifecycle.replace(
        "research.web",
        _binding("replacement"),
        cycle=4,
        reason="replace unreliable implementation",
    )
    lifecycle.record_use(
        CapabilityUsageRecord(
            capability_id="research.web",
            cycle=5,
            binding_id="binding:replacement",
            candidate_id="candidate:replacement",
            success=True,
            evidence_result="COVERED",
        )
    )
    current = inventory.get("research.web")
    assert current.state == CapabilityLifecycleState.ACTIVE
    assert current.health == 1.0
    lifecycle.retire("research.web", cycle=6, reason="test history partition")
    assert inventory.history[-1].verified_usage_count == 1
    assert inventory.history[-1].successful_usage_count == 1


def test_retirement_and_removal_are_distinct_and_dependency_checked() -> None:
    inventory = CapabilityInventory(entries={"research.web": _binding()})
    lifecycle = CapabilityLifecycleService(inventory, [_use(1, False)])

    decision = lifecycle.retire(
        "research.web",
        cycle=2,
        reason="operator revocation",
        evidence_refs=["operator:2"],
    )
    assert decision.reason == "operator revocation"
    assert decision.evidence_refs == ("out:1", "operator:2")
    assert inventory.get("research.web").state == CapabilityLifecycleState.RETIRED

    with pytest.raises(ValueError, match="dependencies"):
        lifecycle.remove("research.web", plan_dependencies=["plan:active"])
    lifecycle.remove("research.web")
    assert inventory.get("research.web") is None
    assert inventory.history[-1].why_retired == "operator revocation"
    assert inventory.history[-1].evidence_refs == ["out:1", "operator:2"]


def test_retirement_provenance_persists_across_runtime_restore(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(state_dir=tmp_path)
    binding = runtime.capability_inventory.get("observe")
    binding.evidence_refs = ["binding:verified"]
    runtime.capability_lifecycle.retire(
        "observe",
        cycle=1,
        reason="provider revoked",
        replacement_reference="binding:replacement",
        evidence_refs=["decision:retire"],
    )
    runtime.persist()

    restored = AgentRuntime.restore(tmp_path)
    history = restored.capability_inventory.history[-1]
    assert history.replacement_reference == "binding:replacement"
    assert history.evidence_refs == ["binding:verified", "decision:retire"]


def test_usage_recording_survives_runtime_history_compaction(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(state_dir=tmp_path)
    runtime.capability_usage = [
        CapabilityUsageRecord(capability_id="observe", cycle=index)
        for index in range(1025)
    ]
    runtime.capability_lifecycle.usage = runtime.capability_usage
    runtime._compact_live_history()

    record = CapabilityUsageRecord(capability_id="observe", cycle=2000)
    runtime.capability_lifecycle.record_use(record)
    assert runtime.capability_usage[-1] is record
