"""Assemble a DecisionRecord from planning + authorization."""
from __future__ import annotations

from datetime import datetime

from ..action.policy import AuthorizationResult
from ..action.proposal import ActionProposal
from ..clock import new_id
from ..records import DecisionRecord


def make_decision(
    *,
    cycle: int,
    timestamp: datetime,
    goal_ids: list[str],
    proposals: list[ActionProposal],
    authorization: AuthorizationResult,
    viability_score: float,
    motivation_urgency: float,
) -> DecisionRecord:
    alternatives = list(dict.fromkeys(p.kind for p in proposals))
    context_refs = list(
        dict.fromkeys(
            ref
            for proposal in proposals
            for ref in proposal.evidence
            if ref
        )
    )
    action_predictions = [
        {
            "action_kind": proposal.kind,
            "prediction": proposal.payload.get("prediction"),
        }
        for proposal in authorization.authorized
        if proposal.payload.get("prediction") is not None
    ]
    if authorization.denied and not authorization.authorized:
        reason = "all proposed actions blocked by autonomy/constitution"
    elif authorization.denied:
        reason = "partial authorization; constitution/policy filtered some actions"
    elif authorization.authorized:
        reason = "authorized planned actions"
    else:
        reason = "no actions proposed"
    return DecisionRecord(
        decision_id=new_id("dec"),
        cycle=cycle,
        timestamp=timestamp,
        goal_ids=goal_ids,
        proposed_actions=[p.kind for p in proposals],
        authorized_actions=[p.kind for p in authorization.authorized],
        denied=list(authorization.denied),
        reason=reason,
        viability_score=viability_score,
        motivation_urgency=motivation_urgency,
        question="Which proposed actions should execute for the active goals?",
        alternatives=alternatives,
        context_refs=context_refs,
        prediction={
            "expectation": "authorized actions should produce verified successful outcomes",
            "action_predictions": action_predictions,
        } if authorization.authorized else {},
        confidence=0.5,
        reversal_conditions=(
            ["verified outcome evidence shows one or more authorized actions failed"]
            if authorization.authorized
            else []
        ),
    )
