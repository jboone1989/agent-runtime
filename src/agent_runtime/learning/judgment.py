"""Outcome-grounded judgment learning.

A decision is not a lesson until linked actions produce verifiable outcomes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import re

from ..experience.refs import stable_ref
from ..records import ActionRecord, DecisionRecord, OutcomeRecord


@dataclass
class JudgmentSettlement:
    settlement_id: str
    decision_id: str
    settled_at: datetime
    status: str
    action_refs: list[str] = field(default_factory=list)
    outcome_refs: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    prediction_error: float | None = None
    confidence_before: float = 0.5
    confidence_after: float = 0.5
    lesson: str = ""


def settle_judgment(
    decision: DecisionRecord,
    *,
    actions: list[ActionRecord],
    outcomes: list[OutcomeRecord],
) -> JudgmentSettlement:
    linked_actions = [
        action for action in actions if action.decision_id == decision.decision_id
    ]
    outcome_by_action = {outcome.action_id: outcome for outcome in outcomes}
    linked_outcomes = [
        outcome_by_action[action.action_id]
        for action in linked_actions
        if action.action_id in outcome_by_action
    ]
    verifiable = [
        outcome
        for outcome in linked_outcomes
        if outcome.evidence_result not in {"UNVERIFIABLE", "UNCOVERED"}
        and not outcome.missing_evidence_units
    ]

    if not decision.authorized_actions:
        status = "UNRESOLVED"
        error = None
        lesson = "No action was authorized, so this judgment has no world outcome yet."
    elif not linked_actions or len(linked_outcomes) != len(linked_actions):
        status = "UNRESOLVED"
        error = None
        lesson = "The judgment is still waiting for a complete linked action outcome."
    elif len(verifiable) != len(linked_outcomes):
        status = "UNRESOLVED"
        error = None
        lesson = "The linked outcome exists but is not sufficiently verified to settle the judgment."
    else:
        successes = sum(outcome.success for outcome in verifiable)
        if successes == len(verifiable):
            status = "CONFIRMED"
            error = 0.0
            lesson = "The chosen action set produced verified successful outcomes."
        elif successes == 0:
            status = "REFUTED"
            error = 1.0
            lesson = "The chosen action set failed under verified outcome evidence."
        else:
            status = "PARTIALLY_CONFIRMED"
            error = 1.0 - (successes / len(verifiable))
            lesson = "The chosen action set produced mixed verified outcomes."

    before = min(1.0, max(0.0, float(decision.confidence)))
    if status == "CONFIRMED":
        after = min(1.0, before + 0.1)
    elif status == "REFUTED":
        after = max(0.0, before - 0.2)
    elif status == "PARTIALLY_CONFIRMED":
        after = max(0.0, before - 0.05)
    else:
        after = before

    action_refs = [action.action_id for action in linked_actions]
    outcome_refs = [outcome.outcome_id for outcome in linked_outcomes]
    evidence_refs = list(
        dict.fromkeys(
            [
                decision.decision_id,
                *decision.context_refs,
                *action_refs,
                *outcome_refs,
            ]
        )
    )
    settled_at = (
        max((outcome.timestamp for outcome in linked_outcomes), default=decision.timestamp)
    )
    return JudgmentSettlement(
        settlement_id=stable_ref(
            "jset",
            decision.decision_id,
            status,
            *outcome_refs,
        ),
        decision_id=decision.decision_id,
        settled_at=settled_at,
        status=status,
        action_refs=action_refs,
        outcome_refs=outcome_refs,
        evidence_refs=evidence_refs,
        prediction_error=error,
        confidence_before=before,
        confidence_after=after,
        lesson=lesson,
    )


def relevant_judgments(
    query: str,
    *,
    decisions: list[DecisionRecord],
    settlements: list[JudgmentSettlement],
    limit: int = 4,
) -> list[dict]:
    if limit <= 0:
        return []
    by_id = {decision.decision_id: decision for decision in decisions}
    tokens = _tokens(query)
    ranked: list[tuple[float, datetime, JudgmentSettlement, DecisionRecord]] = []
    for settlement in settlements:
        if settlement.status == "UNRESOLVED":
            continue
        decision = by_id.get(settlement.decision_id)
        if decision is None:
            continue
        searchable = " ".join(
            [
                decision.question,
                decision.reason,
                *decision.alternatives,
                *decision.authorized_actions,
            ]
        )
        overlap = len(tokens & _tokens(searchable))
        score = float(overlap)
        if decision.goal_ids:
            score += 0.1
        ranked.append((score, settlement.settled_at, settlement, decision))

    if not ranked:
        return []
    matched = [row for row in ranked if row[0] > 0.0]
    pool = matched if matched else ranked[-min(2, len(ranked)) :]
    pool.sort(key=lambda row: (row[0], row[1]), reverse=True)

    result: list[dict] = []
    for _score, _when, settlement, decision in pool[:limit]:
        result.append(
            {
                "decision_id": decision.decision_id,
                "question": decision.question,
                "alternatives": list(decision.alternatives),
                "chosen_actions": list(decision.authorized_actions),
                "reason": decision.reason,
                "status": settlement.status,
                "lesson": settlement.lesson,
                "prediction_error": settlement.prediction_error,
                "confidence_after": settlement.confidence_after,
                "reversal_conditions": list(decision.reversal_conditions),
                "outcome_refs": list(settlement.outcome_refs),
            }
        )
    return result


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[\w-]+", str(text).casefold())
        if len(token) >= 2
    }
