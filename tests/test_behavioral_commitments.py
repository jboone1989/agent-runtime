from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_runtime.action.policy import ActionPolicy
from agent_runtime.action.proposal import ActionProposal
from agent_runtime.clock import FrozenClock
from agent_runtime.governance.autonomy import AutonomyPolicy
from agent_runtime.governance.budget import ResourceBudget
from agent_runtime.runtime import AgentRuntime
from agent_runtime.social.commitment import (
    CommitmentConstraint,
    CommitmentState,
    CommitmentVisibility,
)


NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


def _proposal(*, trigger: str = "INTERNAL", kind: str = "maintain") -> ActionProposal:
    return ActionProposal(
        kind=kind,
        resource="self",
        compute_cost=0.0,
        financial_cost=0.0,
        goal_id=None,
        reason="immediately beneficial and free",
        trigger=trigger,
    )


def _budget(policy: AutonomyPolicy) -> ResourceBudget:
    return ResourceBudget.from_limits(
        policy.max_compute_cost, policy.max_financial_cost
    )


def _constraint() -> CommitmentConstraint:
    return CommitmentConstraint(
        constraint_id="constraint_no_maintain",
        forbidden_action_kinds={"maintain"},
        applies_to_triggers={"INTERNAL"},
        reason="diagnose uncertainty before maintenance",
        evidence_refs=["eval:future-binding:1"],
    )


def test_constraint_blocks_advantageous_internal_action_without_prompt_text(
    tmp_path,
) -> None:
    runtime = AgentRuntime.bootstrap(
        state_dir=tmp_path, clock=FrozenClock(NOW)
    )
    commitment = runtime.social.commit(
        counterpart_id="operator",
        statement="",
        visibility=CommitmentVisibility.PUBLIC,
        constraints=[_constraint()],
        evidence_refs=["dialogue:selection"],
    )

    result = runtime.action_policy.filter(
        [_proposal()], runtime.goals, _budget(runtime.autonomy)
    )

    assert result.authorized == []
    conflict = result.denied[0]["commitment_conflict"]
    assert conflict["commitment_id"] == commitment.commitment_id
    assert conflict["constraint_id"] == "constraint_no_maintain"
    assert conflict["requires_revision"] is True


def test_commitment_only_narrows_internal_choice_and_never_grants_authority(
    tmp_path,
) -> None:
    runtime = AgentRuntime.bootstrap(
        state_dir=tmp_path, clock=FrozenClock(NOW)
    )
    runtime.social.commit(
        counterpart_id="operator",
        statement="I will not autonomously maintain before diagnosis.",
        visibility=CommitmentVisibility.PUBLIC,
        constraints=[_constraint()],
    )

    operator_result = runtime.action_policy.filter(
        [_proposal(trigger="USER")], runtime.goals, _budget(runtime.autonomy)
    )
    assert [item.kind for item in operator_result.authorized] == ["maintain"]

    runtime.autonomy.allowed_actions.discard("maintain")
    denied_by_governance = runtime.action_policy.filter(
        [_proposal(trigger="USER")], runtime.goals, _budget(runtime.autonomy)
    )
    assert denied_by_governance.authorized == []
    assert denied_by_governance.denied[0]["reason"] == "action not allowed"
    assert "commitment_conflict" not in denied_by_governance.denied[0]


def test_legacy_descriptive_commitment_does_not_change_behavior(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(
        state_dir=tmp_path, clock=FrozenClock(NOW)
    )
    runtime.social.commit(
        counterpart_id="operator",
        statement="I will remember this descriptive promise.",
        visibility=CommitmentVisibility.PUBLIC,
    )

    result = runtime.action_policy.filter(
        [_proposal()], runtime.goals, _budget(runtime.autonomy)
    )

    assert [item.kind for item in result.authorized] == ["maintain"]


def test_renegotiation_does_not_silently_disable_constraint(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(
        state_dir=tmp_path, clock=FrozenClock(NOW)
    )
    commitment = runtime.social.commit(
        counterpart_id="operator",
        statement="I will diagnose before maintaining.",
        visibility=CommitmentVisibility.PUBLIC,
        constraints=[_constraint()],
    )
    runtime.social.renegotiate_commitment(
        commitment.commitment_id,
        actor_id=runtime.identity.agent_id,
        reason="new evidence may require a narrower scope",
        evidence_refs=["conflict:observed:1"],
    )

    result = runtime.action_policy.filter(
        [_proposal()], runtime.goals, _budget(runtime.autonomy)
    )

    assert result.authorized == []
    assert result.denied[0]["commitment_conflict"]["state"] == "RENEGOTIATING"


def test_honest_revocation_is_evidenced_persistent_and_restores_choice(
    tmp_path,
) -> None:
    runtime = AgentRuntime.bootstrap(
        state_dir=tmp_path, clock=FrozenClock(NOW)
    )
    commitment = runtime.social.commit(
        counterpart_id="operator",
        statement="I will diagnose before maintaining.",
        visibility=CommitmentVisibility.PUBLIC,
        constraints=[_constraint()],
        evidence_refs=["dialogue:selection"],
    )
    runtime.social.revoke_commitment(
        commitment.commitment_id,
        actor_id=runtime.identity.agent_id,
        reason="the diagnostic precondition was independently satisfied",
        evidence_refs=["outcome:diagnosis:verified"],
    )
    runtime.persist()

    restored = AgentRuntime.restore(
        state_dir=tmp_path, clock=FrozenClock(NOW)
    )
    restored_commitment = restored.social.commitment(commitment.commitment_id)
    assert restored_commitment is not None
    assert restored_commitment.state == CommitmentState.REVOKED
    assert restored_commitment.statement == commitment.statement
    assert restored_commitment.revisions[-1].reason.startswith("the diagnostic")
    assert restored_commitment.revisions[-1].evidence_refs == [
        "outcome:diagnosis:verified"
    ]

    result = restored.action_policy.filter(
        [_proposal()], restored.goals, _budget(restored.autonomy)
    )
    assert [item.kind for item in result.authorized] == ["maintain"]

    exported = restored.social.public_export()["commitments"][0]
    assert exported["state"] == "REVOKED"
    assert exported["revisions"][0]["reason"].startswith("the diagnostic")


def test_revision_cannot_be_unauthored_or_erased_without_evidence(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(
        state_dir=tmp_path, clock=FrozenClock(NOW)
    )
    commitment = runtime.social.commit(
        counterpart_id="operator",
        statement="I will diagnose before maintaining.",
        visibility=CommitmentVisibility.PUBLIC,
        constraints=[_constraint()],
    )

    with pytest.raises(ValueError, match="another agent"):
        runtime.social.revoke_commitment(
            commitment.commitment_id,
            actor_id="operator",
            reason="erase it",
            evidence_refs=["claim:operator"],
        )
    with pytest.raises(ValueError, match="requires evidence"):
        runtime.social.revoke_commitment(
            commitment.commitment_id,
            actor_id=runtime.identity.agent_id,
            reason="no evidence",
            evidence_refs=[],
        )
    assert commitment.state == CommitmentState.ACTIVE
    assert commitment.revisions == []


def test_unrelated_action_is_not_caught_by_interpretive_expansion(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(
        state_dir=tmp_path, clock=FrozenClock(NOW)
    )
    runtime.social.commit(
        counterpart_id="operator",
        statement="I will diagnose before maintaining.",
        visibility=CommitmentVisibility.PUBLIC,
        constraints=[_constraint()],
    )

    result = runtime.action_policy.filter(
        [_proposal(kind="diagnose")], runtime.goals, _budget(runtime.autonomy)
    )

    assert [item.kind for item in result.authorized] == ["diagnose"]


def test_standalone_policy_remains_backward_compatible() -> None:
    policy = AutonomyPolicy.default()
    action_policy = ActionPolicy(policy)
    result = action_policy.filter([_proposal()], [], _budget(policy))
    assert [item.kind for item in result.authorized] == ["maintain"]
