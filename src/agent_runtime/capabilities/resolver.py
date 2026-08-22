"""Capability Resolver — Reuse Before Create. Does not install anything."""
from __future__ import annotations

from dataclasses import dataclass

from ..experience.refs import stable_ref
from .evaluator import CapabilityEvaluator
from .inventory import CapabilityInventory
from .model import (
    CapabilityAssessment,
    CapabilityCandidate,
    CapabilityDecision,
    CapabilityDecisionAction,
    CapabilityKind,
)
from .need import CapabilityNeed
from .policy import CapabilityPolicy
from .search import CapabilitySearchRecord
from .sources.base import CapabilitySource
from .sources.local import is_owned_or_local, is_remote_access


class CapabilityCreateWithoutSearchError(ValueError):
    """CREATE is forbidden unless sources were actually searched."""


@dataclass
class CapabilityResolution:
    decision: CapabilityDecision
    search: CapabilitySearchRecord
    assessments: list[CapabilityAssessment]


class CapabilityResolver:
    def __init__(
        self,
        policy: CapabilityPolicy | None = None,
        evaluator: CapabilityEvaluator | None = None,
    ) -> None:
        self.policy = policy or CapabilityPolicy()
        self.evaluator = evaluator or CapabilityEvaluator()

    def create_without_search(self, capability_id: str) -> None:
        raise CapabilityCreateWithoutSearchError(
            f"CREATE of {capability_id} requires CapabilitySearchRecord evidence"
        )

    def resolve(
        self,
        need: CapabilityNeed,
        *,
        sources: list[CapabilitySource],
        inventory: CapabilityInventory,
        plan_step_id: str | None,
        cycle: int,
    ) -> CapabilityResolution:
        _ = inventory
        requirement = need.requirement
        local_sources = [source for source in sources if source.source_id == "local"]
        other_sources = [source for source in sources if source.source_id != "local"]

        consulted: list[str] = []
        found: list[CapabilityCandidate] = []
        for source in local_sources:
            consulted.append(source.source_id)
            found.extend(_normalize(source.search(requirement)))

        local_hits = [candidate for candidate in found if candidate.source in {"local", "inventory"}]
        assessments = [self.evaluator.assess(requirement, candidate, self.policy) for candidate in local_hits]
        viable_local = [
            (candidate, assessment)
            for candidate, assessment in zip(local_hits, assessments, strict=True)
            if assessment.viable and is_owned_or_local(candidate)
        ]
        if viable_local:
            return self._finish(
                need=need,
                action=CapabilityDecisionAction.REUSE_LOCAL,
                chosen=viable_local[0][0],
                consulted=consulted,
                candidates=found,
                assessments=assessments,
                plan_step_id=plan_step_id,
                cycle=cycle,
            )

        viable_remote = [
            (candidate, assessment)
            for candidate, assessment in zip(local_hits, assessments, strict=True)
            if assessment.viable and is_remote_access(candidate)
        ]
        if viable_remote:
            return self._finish(
                need=need,
                action=CapabilityDecisionAction.DELEGATE_REMOTE,
                chosen=viable_remote[0][0],
                consulted=consulted,
                candidates=found,
                assessments=assessments,
                plan_step_id=plan_step_id,
                cycle=cycle,
            )

        for source in other_sources:
            consulted.append(source.source_id)
            found.extend(_normalize(source.search(requirement)))

        assessments = [self.evaluator.assess(requirement, candidate, self.policy) for candidate in found]
        viable = [
            (candidate, assessment)
            for candidate, assessment in zip(found, assessments, strict=True)
            if assessment.viable and candidate.source not in {"local", "inventory"}
        ]
        delegate_ext = [
            pair
            for pair in viable
            if pair[0].kind == CapabilityKind.REMOTE_AGENT or pair[0].source == "network"
        ]
        if delegate_ext:
            return self._finish(
                need=need,
                action=CapabilityDecisionAction.DELEGATE_REMOTE,
                chosen=_best(delegate_ext),
                consulted=consulted,
                candidates=found,
                assessments=assessments,
                plan_step_id=plan_step_id,
                cycle=cycle,
            )
        adapt = [pair for pair in viable if pair[0].acquisition_mode == "adapt"]
        acquire = [
            pair
            for pair in viable
            if pair[0].acquisition_mode != "adapt"
            and pair[0].kind != CapabilityKind.REMOTE_AGENT
            and pair[0].source != "network"
        ]
        if acquire:
            chosen = _best(acquire)
            return self._finish(
                need=need,
                action=CapabilityDecisionAction.ACQUIRE,
                chosen=chosen,
                consulted=consulted,
                candidates=found,
                assessments=assessments,
                plan_step_id=plan_step_id,
                cycle=cycle,
            )
        if adapt:
            return self._finish(
                need=need,
                action=CapabilityDecisionAction.ADAPT,
                chosen=adapt[0][0],
                consulted=consulted,
                candidates=found,
                assessments=assessments,
                plan_step_id=plan_step_id,
                cycle=cycle,
            )
        if not consulted:
            self.create_without_search(requirement.capability_id)
        return self._finish(
            need=need,
            action=CapabilityDecisionAction.CREATE,
            chosen=None,
            consulted=consulted,
            candidates=found,
            assessments=assessments,
            plan_step_id=plan_step_id,
            cycle=cycle,
        )

    def _finish(
        self,
        *,
        need: CapabilityNeed,
        action: CapabilityDecisionAction,
        chosen: CapabilityCandidate | None,
        consulted: list[str],
        candidates: list[CapabilityCandidate],
        assessments: list[CapabilityAssessment],
        plan_step_id: str | None,
        cycle: int,
    ) -> CapabilityResolution:
        if action == CapabilityDecisionAction.CREATE and not consulted:
            self.create_without_search(need.capability_id)
        search_id = stable_ref(
            "csr", need.capability_id, plan_step_id, cycle, ",".join(consulted)
        )
        rejected = [
            {"candidate_id": assessment.candidate_id, "reasons": list(assessment.hard_fail_reasons)}
            for assessment in assessments
            if assessment.hard_fail_reasons
        ]
        evidence = list(need.evidence_refs)
        if plan_step_id:
            evidence = [plan_step_id, *evidence]
        search = CapabilitySearchRecord(
            search_id=search_id,
            capability_id=need.capability_id,
            plan_step_id=plan_step_id,
            sources_consulted=list(consulted),
            candidate_ids=[candidate.candidate_id for candidate in candidates],
            rejected=rejected,
            cycle=cycle,
            evidence_refs=evidence,
        )
        rationale = action.name.lower().replace("_", " ")
        if action == CapabilityDecisionAction.CREATE:
            rationale = "no viable candidate after search"
        decision = CapabilityDecision(
            capability_id=need.capability_id,
            action=action,
            candidate_id=None if chosen is None else chosen.candidate_id,
            rationale=rationale,
            evidence_refs=evidence,
            plan_step_id=plan_step_id,
            search_id=search_id,
        )
        return CapabilityResolution(decision=decision, search=search, assessments=assessments)


def _best(pairs: list[tuple[CapabilityCandidate, CapabilityAssessment]]) -> CapabilityCandidate:
    ranked = sorted(
        pairs,
        key=lambda pair: (
            -float(pair[1].dimensions.get("task_fit", 0.0)),
            -float(pair[1].dimensions.get("maintenance", 0.0)),
            pair[0].candidate_id,
        ),
    )
    return ranked[0][0]


_SECRET_KEYS = ("api_key", "secret", "token", "password", "provider_handle")


def _normalize(candidates: list[CapabilityCandidate]) -> list[CapabilityCandidate]:
    out: list[CapabilityCandidate] = []
    for candidate in candidates:
        provenance = {
            key: value
            for key, value in dict(candidate.provenance).items()
            if key.lower() not in _SECRET_KEYS
        }
        candidate.provenance = provenance
        out.append(candidate)
    return out
