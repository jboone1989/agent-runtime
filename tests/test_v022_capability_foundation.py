"""v0.2.2 Capability Foundation — DoD per docs/capability_fabric_rules.md.

Proves:
  Agent knows what capability exists locally
  Agent knows what capability is missing
  Agent can persist capability inventory
  Agent records capability gaps
  Agent cannot acquire anything yet (v0.2.2)
  Old v0.2.1 snapshots still restore (P0)
"""
from __future__ import annotations

from pathlib import Path

from agent_runtime.capabilities import (
    BUILTIN_CAPABILITY_IDS,
    CapabilityAccessMode,
    CapabilityBinding,
    CapabilityDecisionAction,
    CapabilityGapStatus,
    CapabilityKind,
    CapabilityLifecycleState,
    CapabilityPolicy,
    SideEffectLevel,
)
from agent_runtime.codec import decode, encode
from agent_runtime.runtime import AgentRuntime, RuntimeSnapshot


def _bootstrap(tmp_path: Path) -> AgentRuntime:
    return AgentRuntime.bootstrap(state_dir=tmp_path / "state")


def test_agent_knows_local_capabilities(tmp_path) -> None:
    rt = _bootstrap(tmp_path)
    ids = set(rt.capability_inventory.usable_ids())
    assert set(BUILTIN_CAPABILITY_IDS) <= ids
    assert rt.capability_inventory.local_ids()  # builtins are OWNED/LOCAL
    assert "introspect" in rt.self_model.capabilities
    assert rt.self_model.capabilities["introspect"].available


def test_agent_knows_missing_capability(tmp_path) -> None:
    rt = _bootstrap(tmp_path)
    assert not rt.capability_inventory.usable("web.search")
    assert rt.capability_inventory.get("web.search") is None


def test_gap_detection_records_missing_capability(tmp_path) -> None:
    from agent_runtime.action.proposal import ActionProposal

    rt = _bootstrap(tmp_path)
    rt.tick()
    proposal = ActionProposal(
        kind="web.search",
        resource="network",
        compute_cost=0.0,
        financial_cost=0.0,
        goal_id=None,
        reason="need external search",
        trigger="INTERNAL",
    )
    rt._detect_capability_gaps([proposal], rt.clock.now())
    gaps = [g for g in rt.capability_gaps if g.capability_id == "web.search"]
    assert len(gaps) == 1
    assert gaps[0].status == CapabilityGapStatus.OPEN
    assert gaps[0].reason
    # dedupe: same gap is not recorded twice
    rt._detect_capability_gaps([proposal], rt.clock.now())
    assert len([g for g in rt.capability_gaps if g.capability_id == "web.search"]) == 1


def test_no_gap_for_builtin_capabilities(tmp_path) -> None:
    rt = _bootstrap(tmp_path)
    rt.tick()
    builtin_only = [g.capability_id for g in rt.capability_gaps]
    assert builtin_only == []
    assert set(BUILTIN_CAPABILITY_IDS) <= set(rt.capability_inventory.usable_ids())


def test_gap_resolves_when_capability_registered(tmp_path) -> None:
    from agent_runtime.action.proposal import ActionProposal

    rt = _bootstrap(tmp_path)
    proposal = ActionProposal(
        kind="web.search",
        resource="network",
        compute_cost=0.0,
        financial_cost=0.0,
        goal_id=None,
        reason="need external search",
        trigger="INTERNAL",
    )
    rt._detect_capability_gaps([proposal], rt.clock.now())
    rt.capability_inventory.register(
        CapabilityBinding(
            capability_id="web.search",
            kind=CapabilityKind.TOOL,
            provider="operator",
            access_mode=CapabilityAccessMode.LOCAL,
            state=CapabilityLifecycleState.ACTIVE,
        )
    )
    rt._detect_capability_gaps([proposal], rt.clock.now())
    gap = next(g for g in rt.capability_gaps if g.capability_id == "web.search")
    assert gap.status == CapabilityGapStatus.RESOLVED


def test_inventory_and_gaps_persist_and_restore(tmp_path) -> None:
    rt = _bootstrap(tmp_path)
    rt.capability_inventory.register(
        CapabilityBinding(
            capability_id="twitter.search",
            kind=CapabilityKind.TOOL,
            provider="Agent-Reach",
            access_mode=CapabilityAccessMode.LOCAL,
            state=CapabilityLifecycleState.ACTIVE,
            version="1.0.0",
        )
    )
    rt.persist()
    restored = AgentRuntime.restore(tmp_path / "state")
    assert restored.capability_inventory.usable("twitter.search")
    assert restored.capability_inventory.get("twitter.search").provider == "Agent-Reach"
    assert restored.capability_inventory.get("twitter.search").version == "1.0.0"
    assert set(BUILTIN_CAPABILITY_IDS) <= set(restored.capability_inventory.usable_ids())


def test_restore_old_v021_snapshot_succeeds(tmp_path) -> None:
    """P0: a snapshot written by v0.2.1 (no capability fields) must restore."""
    rt = _bootstrap(tmp_path)
    rt.tick()
    rt.persist()
    path = tmp_path / "state" / "agent_state.json"
    import json

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["__dc__"].endswith("RuntimeSnapshot")
    raw["data"].pop("capability_inventory", None)
    raw["data"].pop("capability_gaps", None)
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

    restored = AgentRuntime.restore(tmp_path / "state")
    assert restored.capability_inventory.usable("introspect")  # defaults + builtin seed
    assert restored.capability_gaps == []
    assert restored.knows_who_it_is()


def test_agent_cannot_acquire_anything_yet(tmp_path) -> None:
    """v0.2.2 has no acquisition path; default policy is fully closed."""
    policy = CapabilityPolicy()
    assert policy.source_allowed("github") is False
    assert policy.source_allowed("mcp_registry") is False
    assert policy.kind_allowed(CapabilityKind.MCP) is False
    assert policy.may_auto_acquire(SideEffectLevel.READ_ONLY) is False
    assert policy.may_auto_acquire(SideEffectLevel.EXTERNAL_SIDE_EFFECT) is False
    assert policy.may_auto_acquire(SideEffectLevel.IRREVERSIBLE) is False
    inventory = _bootstrap(tmp_path).capability_inventory
    acquire_attrs = [a for a in dir(inventory) if "acquire" in a.lower()]
    assert acquire_attrs == []


def test_decision_priority_is_locked() -> None:
    order = [a.name for a in CapabilityDecisionAction]
    assert order == ["REUSE_LOCAL", "DELEGATE_REMOTE", "ACQUIRE", "ADAPT", "CREATE"]


def test_lifecycle_states_are_defined() -> None:
    states = [s.name for s in CapabilityLifecycleState]
    assert states == [
        "DISCOVERED",
        "INSPECTED",
        "APPROVED",
        "ACQUIRED",
        "VERIFIED",
        "ACTIVE",
        "DEGRADED",
        "QUARANTINED",
        "RETIRED",
    ]


def test_remote_access_is_not_owned(tmp_path) -> None:
    """I can call a remote agent != I possess that capability (Self ≠ World)."""
    rt = _bootstrap(tmp_path)
    rt.capability_inventory.register(
        CapabilityBinding(
            capability_id="software.review",
            kind=CapabilityKind.REMOTE_AGENT,
            provider="agent://bug-review-agent",
            access_mode=CapabilityAccessMode.REMOTE_ACCESS,
            state=CapabilityLifecycleState.ACTIVE,
        )
    )
    inv = rt.capability_inventory
    assert inv.usable("software.review")
    assert "software.review" not in inv.local_ids()
    assert "software.review" in inv.remote_access_ids()


def test_external_claim_does_not_promote_proficiency(tmp_path) -> None:
    rt = _bootstrap(tmp_path)
    rt.capability_inventory.register(
        CapabilityBinding(
            capability_id="expert.coding",
            kind=CapabilityKind.REMOTE_AGENT,
            provider="agent://stranger",
            access_mode=CapabilityAccessMode.REMOTE_ACCESS,
            state=CapabilityLifecycleState.ACTIVE,
        )
    )
    rt.capability_inventory.sync_to_self_model(rt.self_model)
    belief = rt.self_model.capabilities["expert.coding"]
    assert belief.available is True
    assert belief.proficiency <= 0.5  # never auto-promoted by an external claim
    assert belief.uncertainty >= 0.5


def test_capability_models_roundtrip_through_codec() -> None:
    from agent_runtime.capabilities import (
        CapabilityAssessment,
        CapabilityCandidate,
        CapabilityDecision,
        CapabilityGap,
        CapabilityNeed,
        CapabilityRequirement,
    )

    requirement = CapabilityRequirement(
        capability_id="software.rate_limit",
        intent="distributed API rate limiting",
        constraints={"language": "python", "maintained": True},
        required_permissions={"net"},
    )
    need = CapabilityNeed(requirement=requirement, urgency=0.7, evidence_refs=["sig_1"])
    candidate = CapabilityCandidate(
        candidate_id="cand_1",
        capability_id="software.rate_limit",
        kind=CapabilityKind.LIBRARY,
        provider="pypi",
        license="MIT",
        side_effects=SideEffectLevel.READ_ONLY,
        permissions={"net"},
    )
    assessment = CapabilityAssessment(
        candidate_id="cand_1",
        capability_id="software.rate_limit",
        dimensions={"task_fit": 0.9, "confidence": 0.6},
        hard_fail_reasons=[],
    )
    decision = CapabilityDecision(
        capability_id="software.rate_limit",
        action=CapabilityDecisionAction.ACQUIRE,
        candidate_id="cand_1",
        rationale="best fit",
    )
    snapshot = RuntimeSnapshot(
        identity=None,
        self_model=None,
        world_state=None,
        goals=[],
        records=None,
        episodes=[],
        facts={},
        procedures={},
        cycle=0,
        next_action_kind=None,
        last_attribution=None,
        autonomy=None,
        budget=None,
        constitution=None,
        providers=None,
        regulatory_state=None,
    )
    snapshot.capability_inventory.entries["software.rate_limit"] = CapabilityBinding(
        capability_id="software.rate_limit",
        kind=CapabilityKind.LIBRARY,
        provider="pypi",
    )
    snapshot.capability_gaps = [
        CapabilityGap(
            capability_id="x.gap",
            status=CapabilityGapStatus.OPEN,
            cycle=3,
            reason="codec roundtrip",
        )
    ]
    _ = candidate, assessment, decision, need
    restored = decode(encode(snapshot))
    assert restored.capability_inventory.entries["software.rate_limit"].kind == CapabilityKind.LIBRARY
    assert restored.capability_gaps[0].capability_id == "x.gap"
