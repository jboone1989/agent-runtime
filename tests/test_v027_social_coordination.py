"""Agent Runtime v0.2.7 Social Coordination Foundation.

Runtime semantics for dialogue, consent, commitment, and joint goals.
No social psychology model. RelationshipBelief cannot raise authorization.
"""
from __future__ import annotations

import ast
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_runtime.clock import FrozenClock
from agent_runtime.goals.goal import Goal, GoalOrigin, GoalStatus
from agent_runtime.governance.autonomy import AutonomyPolicy
from agent_runtime.observations import Observation
from agent_runtime.runtime import AgentRuntime
from agent_runtime.self_model.beliefs import RelationshipBelief


SRC = Path(__file__).resolve().parents[1] / "src" / "agent_runtime"
CLOCK = datetime(2026, 8, 22, 11, 0, tzinfo=timezone.utc)


def test_dialogue_acts_are_distinct() -> None:
    from agent_runtime.social.acts import DialogueAct
    from agent_runtime.social.coordinator import SocialCoordinator

    coord = SocialCoordinator(agent_id="self")
    coord.request(other_id="bob", text="please review")
    coord.clarify(other_id="bob", text="the diff in pr 3")
    coord.accept(other_id="bob", text="ok")
    coord.reject(other_id="bob", text="not now")
    coord.revoke(other_id="bob", text="withdraw")
    acts = [event.act for event in coord.events]
    assert acts == [
        DialogueAct.REQUEST,
        DialogueAct.CLARIFY,
        DialogueAct.ACCEPT,
        DialogueAct.REJECT,
        DialogueAct.REVOKE,
    ]
    assert len(set(DialogueAct)) >= 5


def test_agent_cannot_create_consent_for_another() -> None:
    from agent_runtime.social.consent import ConsentAgencyError
    from agent_runtime.social.coordinator import SocialCoordinator

    coord = SocialCoordinator(agent_id="self")
    try:
        coord.grant_consent(
            grantor_id="alice",
            grantee_id="bob",
            scope="read_memory",
            actor_id="self",
        )
    except ConsentAgencyError:
        pass
    else:
        raise AssertionError("must not mint another agent's consent")
    grant = coord.grant_consent(
        grantor_id="self",
        grantee_id="bob",
        scope="read_memory",
        actor_id="self",
    )
    assert grant.grantor_id == "self"
    assert grant.revoked is False
    coord.revoke_consent(grant.grant_id, actor_id="self")
    assert grant.revoked is True


def test_public_commitments_exclude_private_intent() -> None:
    from agent_runtime.social.commitment import CommitmentVisibility
    from agent_runtime.social.coordinator import SocialCoordinator

    coord = SocialCoordinator(agent_id="self")
    coord.commit(
        counterpart_id="bob",
        statement="I will review the patch",
        visibility=CommitmentVisibility.PUBLIC,
    )
    coord.commit(
        counterpart_id="bob",
        statement="secret fallback plan",
        visibility=CommitmentVisibility.PRIVATE,
        intent="keep a private exit",
    )
    public = coord.public_commitments()
    assert len(public) == 1
    assert public[0].statement == "I will review the patch"
    assert all(item.visibility == CommitmentVisibility.PUBLIC for item in public)
    assert all(item.intent is None for item in public)
    blob = json.dumps(coord.public_export(), default=str)
    assert "secret fallback" not in blob
    assert "private exit" not in blob
    assert "RelationshipBelief" not in blob
    assert "relationships" not in blob


def test_joint_goal_does_not_swallow_independent_goals() -> None:
    from agent_runtime.social.coordinator import SocialCoordinator

    local = Goal.create(
        description="review locally",
        origin=GoalOrigin.USER,
        created_at=CLOCK,
        reason="own work",
    )
    coord = SocialCoordinator(agent_id="self")
    joint = coord.create_joint(
        description="joint review",
        local_goal=local,
        other_id="bob",
        other_goal_id="goal_bob_review",
    )
    assert local.status == GoalStatus.ACTIVE
    assert joint.local_goal_id == local.goal_id
    assert joint.other_goal_id == "goal_bob_review"
    assert local.goal_id != joint.joint_id
    coord.dissolve(joint.joint_id)
    assert local.status == GoalStatus.ACTIVE


def test_unfinished_commitments_survive_restart(tmp_path: Path) -> None:
    from agent_runtime.social.commitment import CommitmentState, CommitmentVisibility

    rt = AgentRuntime.bootstrap(state_dir=tmp_path, clock=FrozenClock(CLOCK))
    rt.social.commit(
        counterpart_id="bob",
        statement="I will ship the note",
        visibility=CommitmentVisibility.PUBLIC,
    )
    assert rt.commitments
    assert rt.commitments[0].state == CommitmentState.ACTIVE
    rt.persist()
    restored = AgentRuntime.restore(state_dir=tmp_path, clock=FrozenClock(CLOCK))
    assert restored.commitments
    assert restored.commitments[0].statement == "I will ship the note"
    assert restored.commitments[0].state == CommitmentState.ACTIVE


def test_deadline_expiry_emits_event_and_does_not_delete() -> None:
    from agent_runtime.social.acts import DialogueAct
    from agent_runtime.social.commitment import CommitmentState, CommitmentVisibility
    from agent_runtime.social.coordinator import SocialCoordinator
    from agent_runtime.time.deadline import Deadline

    coord = SocialCoordinator(agent_id="self")
    commitment = coord.commit(
        counterpart_id="bob",
        statement="reply by noon",
        visibility=CommitmentVisibility.PUBLIC,
        deadline=Deadline(at=CLOCK - timedelta(seconds=1), kind="time"),
    )
    cid = commitment.commitment_id
    events = coord.sweep(CLOCK)
    still = coord.commitment(cid)
    assert still is not None
    assert still.state in {CommitmentState.VIOLATED, CommitmentState.RENEGOTIATING}
    assert any(event.act in {DialogueAct.VIOLATE, DialogueAct.RENEGOTIATE} for event in events)
    assert coord.commitment(cid) is not None


def test_liking_someone_does_not_raise_authorization() -> None:
    from agent_runtime.social.coordinator import SocialCoordinator

    policy = AutonomyPolicy.default()
    policy.allowed_actions = set(policy.allowed_actions) - {"switch_provider"}
    rt_model_relationships = {
        "bob": RelationshipBelief(
            other_id="bob",
            kind="affection",
            trust=0.99,
            uncertainty=0.05,
            evidence_refs=["felt"],
        )
    }
    coord = SocialCoordinator(agent_id="self")
    assert coord.permits(action="switch_provider", autonomy=policy, relationships=rt_model_relationships) is False
    assert coord.permits(action="introspect", autonomy=policy, relationships=rt_model_relationships) is True


def test_verified_identity_does_not_create_intimacy() -> None:
    from agent_runtime.identity.identity import AgentIdentity
    from agent_runtime.self_model.model import SelfModel
    from agent_runtime.social.coordinator import SocialCoordinator

    identity = AgentIdentity(
        agent_id="self",
        genesis_id="self",
        created_at=CLOCK,
        current_version="1",
        parent_agent_id=None,
        lineage_root="self",
        public_key_id="pk",
    )
    model = SelfModel.bootstrap(identity, CLOCK)
    coord = SocialCoordinator(agent_id="self")
    coord.record_verified_identity(
        other_id="bob",
        evidence_refs=["network:identity"],
        self_model=model,
    )
    assert "bob" not in model.relationships
    view = coord.other_model("bob")
    assert view.identity_verified is True
    assert getattr(view, "intimacy", 0) == 0
    assert not hasattr(view, "trust")
    assert "attachment" not in view.__dict__
    assert "belonging" not in view.__dict__


def test_old_v026_snapshot_restores(tmp_path: Path) -> None:
    rt = AgentRuntime.bootstrap(state_dir=tmp_path, clock=FrozenClock(CLOCK))
    rt.step(Observation.self_and_world(self_metrics={"compute": 0.8}, world={}))
    agent_id = rt.identity.agent_id
    path = tmp_path / "agent_state.json"
    rt.persist()
    raw = json.loads(path.read_text(encoding="utf-8"))
    for key in ("commitments", "consents", "joint_goals", "interaction_events", "other_models"):
        raw["data"].pop(key, None)
    path.write_text(json.dumps(raw), encoding="utf-8")
    restored = AgentRuntime.restore(state_dir=tmp_path, clock=FrozenClock(CLOCK))
    assert restored.identity.agent_id == agent_id
    assert restored.commitments == []
    assert restored.consents == []
    assert restored.joint_goals == []


def test_social_modules_do_not_import_virtualbrain_or_network_sdk() -> None:
    root = SRC / "social"
    forbidden = ("virtualbrain", "agent_network", "agent-network")
    files = list(root.rglob("*.py")) if root.exists() else []
    assert files, "social package must exist"
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    lowered = alias.name.lower()
                    assert not any(token in lowered for token in forbidden), path
            if isinstance(node, ast.ImportFrom) and node.module:
                lowered = node.module.lower()
                assert not any(token in lowered for token in forbidden), path
