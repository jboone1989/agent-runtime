"""Social coordination: dialogue, consent, commitments, joint goals.

Liking, trust feelings, and verified identity do not raise authorization.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from ..clock import new_id
from ..goals.goal import Goal
from ..governance.autonomy import AutonomyPolicy
from ..self_model.beliefs import RelationshipBelief
from ..self_model.model import SelfModel
from ..time.deadline import Deadline
from .acts import DialogueAct, InteractionEvent
from .commitment import Commitment, CommitmentState, CommitmentVisibility
from .consent import ConsentAgencyError, ConsentGrant, ConsentRevocation
from .joint import JointGoal, JointGoalStatus
from .other import OtherModelView, RelationshipEvidence


class SocialCoordinator:
    def __init__(
        self,
        agent_id: str,
        *,
        now: Callable[[], datetime] | None = None,
        commitments: list[Commitment] | None = None,
        consents: list[ConsentGrant] | None = None,
        consent_revocations: list[ConsentRevocation] | None = None,
        joint_goals: list[JointGoal] | None = None,
        events: list[InteractionEvent] | None = None,
        other_models: dict[str, OtherModelView] | None = None,
        relationship_evidence: dict[str, RelationshipEvidence] | None = None,
    ) -> None:
        self.agent_id = agent_id
        self._now = now or (lambda: datetime.now(timezone.utc))
        self.commitments: list[Commitment] = list(commitments or [])
        self.consents: list[ConsentGrant] = list(consents or [])
        self.consent_revocations: list[ConsentRevocation] = list(consent_revocations or [])
        self.joint_goals: list[JointGoal] = list(joint_goals or [])
        self.events: list[InteractionEvent] = list(events or [])
        self.other_models: dict[str, OtherModelView] = dict(other_models or {})
        self.relationship_evidence: dict[str, RelationshipEvidence] = dict(
            relationship_evidence or {}
        )

    def request(self, *, other_id: str, text: str) -> InteractionEvent:
        return self._act(DialogueAct.REQUEST, other_id, text)

    def accept(self, *, other_id: str, text: str) -> InteractionEvent:
        return self._act(DialogueAct.ACCEPT, other_id, text)

    def reject(self, *, other_id: str, text: str) -> InteractionEvent:
        return self._act(DialogueAct.REJECT, other_id, text)

    def revoke(self, *, other_id: str, text: str) -> InteractionEvent:
        return self._act(DialogueAct.REVOKE, other_id, text)

    def clarify(self, *, other_id: str, text: str) -> InteractionEvent:
        return self._act(DialogueAct.CLARIFY, other_id, text)

    def grant_consent(
        self,
        *,
        grantor_id: str,
        grantee_id: str,
        scope: str,
        actor_id: str,
    ) -> ConsentGrant:
        if actor_id != grantor_id:
            raise ConsentAgencyError("an agent cannot create consent for another agent")
        grant = ConsentGrant(
            grant_id=new_id("consent"),
            grantor_id=grantor_id,
            grantee_id=grantee_id,
            scope=scope,
            actor_id=actor_id,
        )
        self.consents.append(grant)
        return grant

    def revoke_consent(self, grant_id: str, *, actor_id: str, reason: str = "") -> ConsentRevocation:
        grant = next((item for item in self.consents if item.grant_id == grant_id), None)
        if grant is None:
            raise ConsentAgencyError(f"unknown grant {grant_id}")
        if actor_id != grant.grantor_id:
            raise ConsentAgencyError("an agent cannot revoke another agent's consent")
        grant.revoked = True
        record = ConsentRevocation(
            revocation_id=new_id("crev"),
            grant_id=grant_id,
            actor_id=actor_id,
            reason=reason,
            timestamp=self._now(),
        )
        self.consent_revocations.append(record)
        return record

    def commit(
        self,
        *,
        counterpart_id: str,
        statement: str,
        visibility: CommitmentVisibility,
        intent: str | None = None,
        deadline: Deadline | None = None,
    ) -> Commitment:
        stored_intent = None if visibility == CommitmentVisibility.PUBLIC else intent
        item = Commitment(
            commitment_id=new_id("cmt"),
            counterpart_id=counterpart_id,
            statement=statement,
            visibility=visibility,
            state=CommitmentState.ACTIVE,
            intent=stored_intent,
            deadline=deadline,
        )
        self.commitments.append(item)
        return item

    def public_commitments(self) -> list[Commitment]:
        exported: list[Commitment] = []
        for item in self.commitments:
            if item.visibility != CommitmentVisibility.PUBLIC:
                continue
            exported.append(replace(item, intent=None))
        return exported

    def public_export(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "commitments": [
                {
                    "commitment_id": item.commitment_id,
                    "counterpart_id": item.counterpart_id,
                    "statement": item.statement,
                    "state": item.state.value,
                    "visibility": item.visibility.value,
                }
                for item in self.commitments
                if item.visibility == CommitmentVisibility.PUBLIC
            ],
            "consents": [
                {
                    "grant_id": grant.grant_id,
                    "grantee_id": grant.grantee_id,
                    "scope": grant.scope,
                }
                for grant in self.consents
                if not grant.revoked and grant.grantor_id == self.agent_id
            ],
        }

    def create_joint(
        self,
        *,
        description: str,
        local_goal: Goal,
        other_id: str,
        other_goal_id: str,
    ) -> JointGoal:
        joint = JointGoal(
            joint_id=new_id("joint"),
            description=description,
            local_goal_id=local_goal.goal_id,
            other_id=other_id,
            other_goal_id=other_goal_id,
        )
        self.joint_goals.append(joint)
        return joint

    def dissolve(self, joint_id: str) -> JointGoal:
        joint = next((item for item in self.joint_goals if item.joint_id == joint_id), None)
        if joint is None:
            raise ValueError(f"unknown joint goal {joint_id}")
        joint.status = JointGoalStatus.DISSOLVED
        return joint

    def commitment(self, commitment_id: str) -> Commitment | None:
        return next(
            (item for item in self.commitments if item.commitment_id == commitment_id),
            None,
        )

    def sweep(self, now: datetime) -> list[InteractionEvent]:
        emitted: list[InteractionEvent] = []
        for item in self.commitments:
            if item.deadline is None:
                continue
            if item.state != CommitmentState.ACTIVE:
                continue
            if item.deadline.at > now:
                continue
            item.state = CommitmentState.VIOLATED
            emitted.append(
                self._act(
                    DialogueAct.VIOLATE,
                    item.counterpart_id,
                    "deadline expired",
                )
            )
        return emitted

    def permits(
        self,
        *,
        action: str,
        autonomy: AutonomyPolicy,
        relationships: dict[str, RelationshipBelief] | None = None,
    ) -> bool:
        del relationships
        return action in autonomy.allowed_actions

    def record_verified_identity(
        self,
        *,
        other_id: str,
        evidence_refs: list[str],
        self_model: SelfModel | None = None,
    ) -> OtherModelView:
        del self_model
        view = self.other_models.get(other_id) or OtherModelView(agent_id=other_id)
        view.identity_verified = True
        for ref in evidence_refs:
            if ref not in view.evidence_refs:
                view.evidence_refs.append(ref)
        self.other_models[other_id] = view
        return view

    def other_model(self, other_id: str) -> OtherModelView:
        view = self.other_models.get(other_id)
        if view is None:
            raise KeyError(other_id)
        return view

    def _act(self, act: DialogueAct, other_id: str, text: str) -> InteractionEvent:
        event = InteractionEvent(
            event_id=new_id("iact"),
            act=act,
            actor_id=self.agent_id,
            other_id=other_id,
            text=text,
            timestamp=self._now(),
        )
        self.events.append(event)
        evidence = self.relationship_evidence.get(other_id) or RelationshipEvidence(
            other_id=other_id
        )
        evidence.interaction_count += 1
        evidence.last_act = act.value
        evidence.evidence_refs.append(event.event_id)
        self.relationship_evidence[other_id] = evidence
        return event
