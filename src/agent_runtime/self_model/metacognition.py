"""Evidence-backed knowledge boundaries and confidence calibration."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from ..clock import new_id
from ..memory.governance import MemoryQuery
from ..memory.store import MemoryStore
from ..cognition.world_model import WorldState
from .beliefs import KnowledgeAssessment
from .model import SelfModel


def assess_knowledge(
    model: SelfModel,
    *,
    query: str,
    timestamp: datetime,
    memory: MemoryStore | None = None,
    world: WorldState | None = None,
) -> KnowledgeAssessment:
    value, confidence, basis = _lookup(model, query, memory=memory, world=world)
    answerable = bool(basis)
    assessment = KnowledgeAssessment(
        assessment_id=new_id("kas"),
        query=query,
        value=value if answerable else None,
        answerable=answerable,
        confidence=(
            max(0.0, min(1.0, confidence))
            if answerable
            else 0.95
        ),
        timestamp=timestamp,
        basis=basis,
    )
    model.knowledge_assessments.append(assessment)
    return assessment


def resolve_assessment(
    assessment: KnowledgeAssessment,
    *,
    expected_value: Any = None,
    expected_answerable: bool = True,
    evidence_refs: list[str] | None = None,
) -> KnowledgeAssessment:
    correct = (
        assessment.answerable == expected_answerable
        and (
            not expected_answerable
            or assessment.value == expected_value
        )
    )
    assessment.resolved_correct = correct
    assessment.calibration_error = (
        assessment.confidence - (1.0 if correct else 0.0)
    ) ** 2
    assessment.resolution_evidence = list(evidence_refs or [])
    return assessment


def calibration_score(model: SelfModel) -> float | None:
    errors = [
        item.calibration_error
        for item in model.knowledge_assessments
        if item.calibration_error is not None
    ]
    for prediction in model.future_predictions:
        if prediction.status not in {"MATCHED", "VIOLATED"}:
            continue
        correct = prediction.status == "MATCHED"
        errors.append((prediction.confidence - (1.0 if correct else 0.0)) ** 2)
    if not errors:
        return None
    return max(0.0, min(1.0, 1.0 - sum(errors) / len(errors)))


def _lookup(
    model: SelfModel,
    query: str,
    *,
    memory: MemoryStore | None,
    world: WorldState | None,
) -> tuple[Any, float, list[str]]:
    if query == "identity.agent_id":
        return model.identity.agent_id, 1.0 - model.identity.uncertainty, ["self:identity"]
    if query == "identity.genesis_id":
        return model.identity.genesis_id, 1.0 - model.identity.uncertainty, ["self:identity"]
    prefix, separator, name = query.partition(".")
    if separator and prefix == "internal_state":
        belief = model.internal_state.get(name)
        if belief is not None:
            return belief.value, 1.0 - belief.uncertainty, list(belief.evidence_refs)
    if separator and prefix == "resources":
        belief = model.resources.get(name)
        if belief is not None:
            return belief.amount, 1.0 - belief.uncertainty, list(belief.evidence_refs)
    if separator and prefix == "capabilities":
        belief = model.capabilities.get(name)
        if belief is not None:
            return belief.available, 1.0 - belief.uncertainty, list(belief.evidence_refs)
    if separator and prefix == "world" and world is not None and name in world.environment:
        return world.environment[name], 0.7, [f"world:{name}"]
    if separator and prefix == "memory" and memory is not None:
        if memory.governor is not None:
            items = memory.governor.query(MemoryQuery(kind="semantic", key=name))
            if items:
                item = items[-1]
                return item.content, item.confidence, [item.item_id]
        elif name in memory.semantic.facts:
            return memory.semantic.facts[name], 0.7, [f"memory:{name}"]
    return None, 0.0, []
