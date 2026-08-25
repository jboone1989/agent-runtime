from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_runtime.action.policy import ActionPolicy
from agent_runtime.action.proposal import ActionProposal
from agent_runtime.capabilities.acquisition.models import AcquisitionTransaction
from agent_runtime.capabilities.acquisition.service import AcquisitionService
from agent_runtime.capabilities.inventory import CapabilityInventory
from agent_runtime.capabilities.model import (
    CapabilityAccessMode,
    CapabilityBinding,
    CapabilityKind,
    CapabilityLifecycleState,
)
from agent_runtime.capabilities.network.claims import NetworkCapabilityClaim
from agent_runtime.capabilities.network.delegation import DelegationService
from agent_runtime.capabilities.network.models import VerificationReceipt
from agent_runtime.clock import FrozenClock, SystemClock
from agent_runtime.codec import decode, dump, encode
from agent_runtime.cognition.world_model import WorldModel, WorldState
from agent_runtime.goals.goal import Goal, GoalOrigin, GoalStatus
from agent_runtime.governance.autonomy import AutonomyPolicy
from agent_runtime.governance.budget import ResourceBudget
from agent_runtime.identity import create_genesis_identity
from agent_runtime.lifecycle.checkpoint import CheckpointCoordinator, CheckpointIdentityError
from agent_runtime.memory.governance import (
    MemoryGovernor,
    MemoryScope,
    MemoryWriteProposal,
)
from agent_runtime.memory.view import MemoryView
from agent_runtime.observations import Observation
from agent_runtime.runtime import AgentRuntime
from agent_runtime.self_model.model import SelfModel
from agent_runtime.survival.failover import ProviderPool
from agent_runtime.viability.state import ViabilityAssessment


NOW = datetime(2026, 8, 22, 15, 0, tzinfo=timezone.utc)


def _proposal(**overrides) -> ActionProposal:
    data = dict(
        kind="introspect",
        resource="self",
        compute_cost=0.1,
        financial_cost=0.0,
        goal_id=None,
        reason="audit",
    )
    data.update(overrides)
    return ActionProposal(**data)


@pytest.mark.parametrize("cost", [-0.1, float("nan"), float("inf")])
def test_action_policy_rejects_invalid_cost_without_mutating_budget(cost: float) -> None:
    policy = ActionPolicy(AutonomyPolicy.default())
    budget = ResourceBudget.from_limits(1.0, 0.0)
    result = policy.filter([_proposal(compute_cost=cost)], [], budget)
    assert result.authorized == []
    assert budget.remaining_compute == 1.0
    assert "finite" in result.denied[0]["reason"]


def test_action_policy_rejects_unknown_goal_origin_instead_of_raising() -> None:
    result = ActionPolicy(AutonomyPolicy.default()).filter(
        [_proposal(goal_origin="FORGED")],
        [],
        ResourceBudget.from_limits(1.0, 0.0),
    )
    assert result.authorized == []
    assert result.denied[0]["reason"] == "invalid goal origin"


def test_failover_uses_healthiest_selected_provider(tmp_path: Path) -> None:
    pool = ProviderPool(active="primary", names=["primary", "bad-backup", "healthy-backup"])
    rt = AgentRuntime.bootstrap(
        tmp_path,
        clock=FrozenClock(NOW),
        providers=pool,
    )
    rt.sense_world.providers = {
        "primary": {"error_rate": 0.9, "available": True, "latency_ms": 20.0},
        "bad-backup": {"error_rate": 0.8, "available": True, "latency_ms": 20.0},
        "healthy-backup": {"error_rate": 0.01, "available": True, "latency_ms": 20.0},
    }
    rt.tick()
    assert pool.active == "healthy-backup"


def test_failover_does_not_select_unconfigured_provider(tmp_path: Path) -> None:
    pool = ProviderPool(active="primary", names=["primary", "backup"])
    rt = AgentRuntime.bootstrap(
        tmp_path,
        clock=FrozenClock(NOW),
        providers=pool,
    )
    rt.sense_world.providers = {
        "primary": {"error_rate": 0.9, "available": True, "latency_ms": 20.0},
        "backup": {"error_rate": 0.8, "available": True, "latency_ms": 20.0},
        "rogue": {"error_rate": 0.0, "available": True, "latency_ms": 20.0},
    }
    result = rt.tick()
    assert pool.active == "primary"
    assert all(action.kind != "switch_provider" for action in result.local_actions)
    assert any(item.code == "COGNITIVE_CAPABILITY_AT_RISK" for item in result.escalations)


def _claim() -> NetworkCapabilityClaim:
    return NetworkCapabilityClaim(
        claim_id="claim",
        capability_id="review",
        provider_agent_id="agent-a",
        artifact_revision="rev-2",
    )


def _receipt(
    receipt_id: str,
    verifier_id: str,
    *,
    contract_id: str = "contract",
) -> VerificationReceipt:
    return VerificationReceipt(
        receipt_id=receipt_id,
        contract_id=contract_id,
        artifact_revision="rev-2",
        accepted=True,
        source="network",
        verifier_id=verifier_id,
    )


def test_delegation_quorum_requires_distinct_verifiers_and_bound_contract() -> None:
    service = DelegationService()
    duplicate = service.accept(
        _claim(),
        receipts=[_receipt("r1", "same"), _receipt("r2", "same")],
        quorum=2,
        expected_contract_id="contract",
    )
    assert duplicate.success is False
    wrong_contract = service.accept(
        _claim(),
        receipts=[_receipt("r3", "v1", contract_id="other")],
        quorum=1,
        expected_contract_id="contract",
    )
    assert wrong_contract.success is False
    assert wrong_contract.reason == "contract mismatch"
    assert service.accept(_claim(), receipts=[], quorum=0).success is False


def test_delegation_rejects_unversioned_artifact() -> None:
    claim = _claim()
    claim.artifact_revision = ""
    result = DelegationService().accept(
        claim,
        receipts=[_receipt("r1", "v1")],
        quorum=1,
    )
    assert result.success is False
    assert "revision" in result.reason


def test_memory_view_keeps_semantic_kind_separate_and_records_updates() -> None:
    governor = MemoryGovernor()
    semantic = governor.commit(
        MemoryWriteProposal(
            proposal_id="s1",
            kind="semantic",
            key="shared-key",
            value="semantic value",
            scope=MemoryScope.PRIVATE,
            source="runtime",
            confidence=0.6,
            evidence_refs=["first"],
        )
    )
    governor.commit(
        MemoryWriteProposal(
            proposal_id="e1",
            kind="episodic",
            key="shared-key",
            value="episodic value",
            scope=MemoryScope.PRIVATE,
            source="runtime",
            confidence=0.99,
            evidence_refs=["episode"],
        )
    )
    updated = governor.commit(
        MemoryWriteProposal(
            proposal_id="s2",
            kind="semantic",
            key="shared-key",
            value="updated semantic",
            scope=MemoryScope.SHARED,
            source="runtime",
            confidence=0.7,
            evidence_refs=["second"],
        )
    )
    assert updated is semantic
    assert [e.refs for e in updated.evidence] == [["first"], ["second"]]
    assert governor.as_view().facts["shared-key"] == "updated semantic"


def test_model_change_tracking_and_internal_update_are_real() -> None:
    identity = create_genesis_identity("1", FrozenClock(NOW))
    model = SelfModel.bootstrap(identity, NOW)
    observation = Observation.self_and_world(
        self_metrics={"compute": 0.7},
        world={"mode": "steady"},
        timestamp=NOW,
    )
    assert model.update_from_observation(observation) == ["compute"]
    assert model.update_from_observation(observation) == []
    world = WorldModel()
    assert world.update(observation) == ["mode"]
    assert world.update(observation) == []
    model.apply_update(
        "internal_state.note",
        "stored",
        "mind update",
        0.2,
        ["e1"],
        timestamp=NOW,
    )
    assert model.internal_state["note"].value == "stored"
    assert model.internal_state["note"].last_updated == NOW


def test_learning_rejects_unsupported_self_model_path_without_partial_write() -> None:
    from agent_runtime.learning.controller import LearningController

    identity = create_genesis_identity("1", FrozenClock(NOW))
    model = SelfModel.bootstrap(identity, NOW)
    result = LearningController().propose_self_model(
        path="identity.agent_id",
        new_value="impostor",
        reason="invalid",
        uncertainty=0.1,
        evidence_refs=["bad"],
        self_model=model,
        timestamp=NOW,
    )
    assert result.accepted is False
    assert "identity.agent_id" not in model.uncertainty
    assert model.identity.agent_id == identity.agent_id


def test_virtualbrain_zero_bias_values_are_not_replaced_by_defaults() -> None:
    from agent_runtime.integrations.virtualbrain import VirtualBrainMotivationEngine

    class Brain:
        motivation_state = SimpleNamespace(
            exploration_bias=0.0,
            persistence=0.0,
            approach_avoid_bias=0.0,
            risk_preference=0.0,
        )

        def pad(self):
            return (0.0, 0.0, 0.0)

        def threat_level(self):
            return 0.0

        def opportunity_level(self):
            return 0.0

        def needs_pressures(self):
            return {}

        def emotion_label(self):
            return ""

        def rpe(self):
            return 0.0

    identity = create_genesis_identity("1", SystemClock())
    state = VirtualBrainMotivationEngine(Brain()).process(
        self_model=SelfModel.bootstrap(identity, identity.created_at),
        viability=ViabilityAssessment(1.0, [], [], 0.0, 0.0),
        world_state=WorldState(timestamp=NOW),
        event=None,
        memory=MemoryView(0, [], {}),
    )
    assert state.exploration_bias == 0.0
    assert state.persistence == 0.0


class _Participant:
    participant_id = "runtime"
    schema_version = "1"

    def snapshot(self) -> dict:
        return {"cycle": 1}

    def restore(self, payload: dict) -> None:
        self.payload = payload


def test_checkpoint_rejects_unknown_envelope_version(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.json"
    coordinator = CheckpointCoordinator(
        agent_id="agent",
        path=path,
        participants=[_Participant()],
        clock=FrozenClock(NOW),
    )
    coordinator.checkpoint(logical_cycle=1)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["checkpoint_version"] = "999"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(CheckpointIdentityError, match="checkpoint_version"):
        coordinator.restore()


def test_codec_rejects_external_types_and_nonfinite_json(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="outside agent_runtime"):
        decode({"__dc__": "pathlib.Path", "data": {"args": ["unsafe"]}})
    with pytest.raises(ValueError):
        dump(float("nan"), tmp_path / "state.json")


def test_codec_marks_string_enums_and_restores_legacy_plain_values() -> None:
    assert encode(GoalOrigin.USER) == {
        "__enum__": "agent_runtime.goals.goal.GoalOrigin",
        "value": "USER",
    }
    restored = decode(
        {
            "__dc__": "agent_runtime.goals.goal.Goal",
            "data": {
                "goal_id": "goal_legacy",
                "description": "resume after upgrade",
                "origin": "USER",
                "status": "ACTIVE",
                "created_at": {"__dt__": NOW.isoformat()},
                "reason": "operator submitted",
            },
        }
    )
    assert isinstance(restored, Goal)
    assert restored.origin is GoalOrigin.USER
    assert restored.status is GoalStatus.ACTIVE


def test_rollback_cannot_retire_an_unrelated_active_binding() -> None:
    inventory = CapabilityInventory()
    inventory.register(
        CapabilityBinding(
            capability_id="review",
            kind=CapabilityKind.SKILL,
            provider="operator",
            access_mode=CapabilityAccessMode.LOCAL,
            state=CapabilityLifecycleState.ACTIVE,
            version="1",
            entry_point={"digest": "trusted", "version": "1"},
        )
    )
    unrelated = AcquisitionTransaction(
        transaction_id="tx",
        plan_id="plan",
        capability_id="review",
        candidate_id="candidate",
        state=CapabilityLifecycleState.ACQUIRED,
        content_digest="other",
        version="2",
        bound_version="2",
    )
    with pytest.raises(ValueError, match="active inventory binding"):
        AcquisitionService().rollback(
            unrelated,
            inventory=inventory,
            cycle=1,
            reason="must not clobber",
        )
    assert inventory.usable("review")
