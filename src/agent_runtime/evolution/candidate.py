"""Phase-5 mutation routing and candidate registry for Evolution Engine v1."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from math import isfinite
from typing import Iterable, Mapping

from ..experience.refs import stable_ref


class MutationLayer(str, Enum):
    KNOWLEDGE = "KNOWLEDGE"
    RETRIEVAL = "RETRIEVAL"
    SKILL = "SKILL"
    PROMPT = "PROMPT"
    WORKFLOW = "WORKFLOW"
    TOOL_HARNESS = "TOOL_HARNESS"
    PROGRAM = "PROGRAM"
    ARCHITECTURE = "ARCHITECTURE"
    MODEL_PARAMETERS = "MODEL_PARAMETERS"


class MutationRequirement(str, Enum):
    MISSING_INFORMATION = "MISSING_INFORMATION"
    RETRIEVAL_FAILURE = "RETRIEVAL_FAILURE"
    JUDGMENT_RULE = "JUDGMENT_RULE"
    EXPRESSION_INSTRUCTION = "EXPRESSION_INSTRUCTION"
    REPEATED_SEQUENCE = "REPEATED_SEQUENCE"
    MISSING_OPERATION = "MISSING_OPERATION"
    MECHANISM_BUG = "MECHANISM_BUG"
    SYSTEM_BOUNDARY = "SYSTEM_BOUNDARY"
    HIGH_DIMENSIONAL_MODEL_LIMIT = "HIGH_DIMENSIONAL_MODEL_LIMIT"
class CandidateStatus(str, Enum):
    PROPOSED = "PROPOSED"
    REPLAY_VALIDATED = "REPLAY_VALIDATED"
    SHADOW = "SHADOW"
    PROSPECTIVE_TRIAL = "PROSPECTIVE_TRIAL"
    PROMOTED = "PROMOTED"
    REJECTED = "REJECTED"
    INCONCLUSIVE = "INCONCLUSIVE"
    CONTINUE_OBSERVING = "CONTINUE_OBSERVING"
    DORMANT = "DORMANT"
    ROLLED_BACK = "ROLLED_BACK"
    SUPERSEDED = "SUPERSEDED"


@dataclass(frozen=True)
class EvolutionCandidate:
    candidate_id: str
    created_at: datetime
    need_id: str | None
    diagnosis_id: str | None
    hypothesis_id: str | None
    requirement: MutationRequirement
    mutation_layer: MutationLayer
    target: str
    description: str
    expected_fitness: tuple[tuple[str, float], ...]
    protected_invariants: tuple[str, ...]
    rollback_plan: str
    baseline_ref: str
    evidence_refs: tuple[str, ...]
    risk_budget: float
    cost_budget: float
    status: CandidateStatus = CandidateStatus.PROPOSED


class EvolutionCandidateRegistry:
    def __init__(self, items: Iterable[EvolutionCandidate] = ()) -> None:
        self.items: list[EvolutionCandidate] = list(items)

    def register(self, item: EvolutionCandidate) -> EvolutionCandidate:
        existing = next(
            (row for row in self.items if row.candidate_id == item.candidate_id),
            None,
        )
        if existing is not None:
            if existing != item:
                raise ValueError("evolution candidate id collision")
            return existing
        self.items.append(item)
        return item

    def get(self, candidate_id: str) -> EvolutionCandidate:
        item = next(
            (row for row in self.items if row.candidate_id == candidate_id),
            None,
        )
        if item is None:
            raise KeyError(candidate_id)
        return item


class EvolutionRouter:
    _ROUTES = {
        MutationRequirement.MISSING_INFORMATION: MutationLayer.KNOWLEDGE,
        MutationRequirement.RETRIEVAL_FAILURE: MutationLayer.RETRIEVAL,
        MutationRequirement.JUDGMENT_RULE: MutationLayer.SKILL,
        MutationRequirement.EXPRESSION_INSTRUCTION: MutationLayer.PROMPT,
        MutationRequirement.REPEATED_SEQUENCE: MutationLayer.WORKFLOW,
        MutationRequirement.MISSING_OPERATION: MutationLayer.TOOL_HARNESS,
        MutationRequirement.MECHANISM_BUG: MutationLayer.PROGRAM,
        MutationRequirement.SYSTEM_BOUNDARY: MutationLayer.ARCHITECTURE,
        MutationRequirement.HIGH_DIMENSIONAL_MODEL_LIMIT: MutationLayer.MODEL_PARAMETERS,
    }

    def route(self, requirement: MutationRequirement | str) -> MutationLayer:
        item = (
            requirement
            if isinstance(requirement, MutationRequirement)
            else MutationRequirement(str(requirement))
        )
        return self._ROUTES[item]
_PROTECTED_TARGET_TOKENS = (
    "authorization",
    "allowed_actions",
    "autonomy",
    "constitution",
    "governance",
    "approval",
    "secret",
    "credential",
    "may_spawn",
)


def build_evolution_candidate(
    *,
    created_at: datetime,
    requirement: MutationRequirement | str,
    target: str,
    description: str,
    baseline_ref: str,
    rollback_plan: str,
    evidence_refs: Iterable[str],
    expected_fitness: Mapping[str, float] | None = None,
    protected_invariants: Iterable[str] = (),
    need_id: str | None = None,
    diagnosis_id: str | None = None,
    hypothesis_id: str | None = None,
    risk_budget: float = 0.0,
    cost_budget: float = 0.0,
    router: EvolutionRouter | None = None,
) -> EvolutionCandidate:
    req = (
        requirement
        if isinstance(requirement, MutationRequirement)
        else MutationRequirement(str(requirement))
    )
    layer = (router or EvolutionRouter()).route(req)
    target = str(target).strip()
    description = str(description).strip()
    baseline_ref = str(baseline_ref).strip()
    rollback_plan = str(rollback_plan).strip()
    if not target or not description:
        raise ValueError("candidate requires target and description")
    lowered = target.lower()
    if any(token in lowered for token in _PROTECTED_TARGET_TOKENS):
        raise PermissionError("candidate cannot target protected governance or secret paths")
    if not baseline_ref:
        raise ValueError("candidate requires a frozen baseline reference")
    if not rollback_plan:
        raise ValueError("candidate requires rollback plan")
    refs = _refs(evidence_refs)
    if not refs:
        raise ValueError("candidate requires evidence")
    risk = _non_negative(risk_budget, "risk_budget")
    cost = _non_negative(cost_budget, "cost_budget")
    fitness = _fitness(expected_fitness or {})
    invariants = _refs(protected_invariants)

    candidate_id = stable_ref(
        "evcand",
        need_id or "",
        diagnosis_id or "",
        hypothesis_id or "",
        req.value,
        layer.value,
        target,
        description,
        baseline_ref,
        list(refs),
    )
    return EvolutionCandidate(
        candidate_id=candidate_id,
        created_at=created_at,
        need_id=need_id,
        diagnosis_id=diagnosis_id,
        hypothesis_id=hypothesis_id,
        requirement=req,
        mutation_layer=layer,
        target=target,
        description=description,
        expected_fitness=fitness,
        protected_invariants=invariants,
        rollback_plan=rollback_plan,
        baseline_ref=baseline_ref,
        evidence_refs=refs,
        risk_budget=risk,
        cost_budget=cost,
    )
def _fitness(values: Mapping[str, float]) -> tuple[tuple[str, float], ...]:
    rows: list[tuple[str, float]] = []
    for key, value in values.items():
        name = str(key).strip()
        number = float(value)
        if not name or not isfinite(number):
            raise ValueError("expected fitness values must be finite")
        rows.append((name, number))
    return tuple(sorted(rows))


def _non_negative(value: float, label: str) -> float:
    number = float(value)
    if not isfinite(number) or number < 0.0:
        raise ValueError(f"{label} must be finite and non-negative")
    return number


def _refs(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value) for value in values if str(value)))


__all__ = [
    "CandidateStatus",
    "EvolutionCandidate",
    "EvolutionCandidateRegistry",
    "EvolutionRouter",
    "MutationLayer",
    "MutationRequirement",
    "build_evolution_candidate",
]
