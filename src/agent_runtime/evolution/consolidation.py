"""Phase-8 sleep/consolidation for Evolution Engine v1."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Iterable

from ..experience.refs import stable_ref
from .capability import CapabilityState, CapabilityVersion
from .evidence import EvolutionEvidence, EvidenceStatus


class EvidenceAge(str, Enum):
    FRESH = "FRESH"
    AGING = "AGING"
    STALE = "STALE"


@dataclass(frozen=True)
class ConsolidationPolicy:
    min_recurrence: int = 2
    aging_after: timedelta = timedelta(days=14)
    stale_after: timedelta = timedelta(days=30)
    deprecate_after: timedelta = timedelta(days=90)


@dataclass(frozen=True)
class ConsolidationCluster:
    cluster_id: str
    signature: str
    evidence_refs: tuple[str, ...]
    experience_refs: tuple[str, ...]
    positive_count: int
    negative_count: int
    unknown_count: int
    recurring: bool


@dataclass(frozen=True)
class ConsolidationConflict:
    conflict_id: str
    cluster_id: str
    summary: str
    evidence_refs: tuple[str, ...]
@dataclass(frozen=True)
class DeprecationProposal:
    proposal_id: str
    capability_id: str
    version_id: str
    reason: str
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class ConsolidationSuggestion:
    suggestion_id: str
    cluster_id: str
    statement: str
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class EvidenceAgeRecord:
    evidence_id: str
    age: EvidenceAge


@dataclass(frozen=True)
class ConsolidationReport:
    report_id: str
    created_at: datetime
    clusters: tuple[ConsolidationCluster, ...]
    conflicts: tuple[ConsolidationConflict, ...]
    deprecation_proposals: tuple[DeprecationProposal, ...]
    suggestions: tuple[ConsolidationSuggestion, ...]
    evidence_ages: tuple[EvidenceAgeRecord, ...]


class EvolutionConsolidator:
    def __init__(self, policy: ConsolidationPolicy | None = None) -> None:
        self.policy = policy or ConsolidationPolicy()

    def run(
        self,
        *,
        now: datetime,
        evidence: Iterable[EvolutionEvidence],
        capabilities: Iterable[CapabilityVersion] = (),
    ) -> ConsolidationReport:
        evidence_rows = tuple(evidence)
        capability_rows = tuple(capabilities)
        clusters = self._clusters(evidence_rows)
        conflicts = tuple(
            ConsolidationConflict(
                conflict_id=stable_ref(
                    "evconflict",
                    cluster.cluster_id,
                    list(cluster.evidence_refs),
                ),
                cluster_id=cluster.cluster_id,
                summary=(
                    "Repeated evidence contains both positive and negative outcomes; "
                    "retain competing explanations until a discriminating experiment settles them."
                ),
                evidence_refs=cluster.evidence_refs,
            )
            for cluster in clusters
            if cluster.positive_count > 0 and cluster.negative_count > 0
        )
        suggestions = tuple(
            ConsolidationSuggestion(
                suggestion_id=stable_ref(
                    "evsuggest",
                    cluster.cluster_id,
                    list(cluster.evidence_refs),
                ),
                cluster_id=cluster.cluster_id,
                statement=(
                    f"Investigate recurring evidence cluster '{cluster.signature}' "
                    "with a bounded diagnosis/candidate cycle."
                ),
                evidence_refs=cluster.evidence_refs,
            )
            for cluster in clusters
            if cluster.recurring
        )
        ages = tuple(
            EvidenceAgeRecord(
                evidence_id=item.evidence_id,
                age=self._age(now, item.settled_at),
            )
            for item in evidence_rows
        )
        deprecations = tuple(
            self._deprecation(now, item)
            for item in capability_rows
            if self._should_propose_deprecation(now, item)
        )
        report_id = stable_ref(
            "evsleep",
            now.isoformat(),
            [row.cluster_id for row in clusters],
            [row.conflict_id for row in conflicts],
            [row.proposal_id for row in deprecations],
            [row.suggestion_id for row in suggestions],
        )
        return ConsolidationReport(
            report_id=report_id,
            created_at=now,
            clusters=clusters,
            conflicts=conflicts,
            deprecation_proposals=deprecations,
            suggestions=suggestions,
            evidence_ages=ages,
        )
    def _clusters(
        self,
        evidence: tuple[EvolutionEvidence, ...],
    ) -> tuple[ConsolidationCluster, ...]:
        grouped: dict[str, list[EvolutionEvidence]] = {}
        for item in evidence:
            if item.status is not EvidenceStatus.VERIFIED:
                continue
            signature = _signature(item)
            grouped.setdefault(signature, []).append(item)
        rows: list[ConsolidationCluster] = []
        for signature, members in sorted(grouped.items()):
            refs = _refs(item.evidence_id for item in members)
            experiences = _refs(item.experience_id for item in members)
            positive = sum(item.outcome_success is True for item in members)
            negative = sum(item.outcome_success is False for item in members)
            unknown = len(members) - positive - negative
            rows.append(
                ConsolidationCluster(
                    cluster_id=stable_ref("evcluster", signature, list(refs)),
                    signature=signature,
                    evidence_refs=refs,
                    experience_refs=experiences,
                    positive_count=positive,
                    negative_count=negative,
                    unknown_count=unknown,
                    recurring=len(experiences) >= self.policy.min_recurrence,
                )
            )
        return tuple(rows)

    def _age(self, now: datetime, settled_at: datetime) -> EvidenceAge:
        delta = max(timedelta(0), now - settled_at)
        if delta >= self.policy.stale_after:
            return EvidenceAge.STALE
        if delta >= self.policy.aging_after:
            return EvidenceAge.AGING
        return EvidenceAge.FRESH

    def _should_propose_deprecation(
        self,
        now: datetime,
        item: CapabilityVersion,
    ) -> bool:
        if item.state not in {CapabilityState.ACTIVE, CapabilityState.DORMANT}:
            return False
        reference = item.last_verified_at or item.born_at
        return now - reference >= self.policy.deprecate_after
    @staticmethod
    def _deprecation(
        now: datetime,
        item: CapabilityVersion,
    ) -> DeprecationProposal:
        refs = _refs(item.supporting_evidence_refs)
        return DeprecationProposal(
            proposal_id=stable_ref(
                "evdeprecate",
                item.capability_id,
                item.version_id,
                now.isoformat(),
                list(refs),
            ),
            capability_id=item.capability_id,
            version_id=item.version_id,
            reason=(
                "Capability has not been verified within the consolidation "
                "deprecation window; propose validation or deprecation."
            ),
            evidence_refs=refs,
        )


def _signature(item: EvolutionEvidence) -> str:
    metric_names = ",".join(metric.name for metric in item.metrics) or "no-metrics"
    return "|".join(
        (
            item.evidence_class.value,
            metric_names,
            "outcome" if item.outcome_success is not None else "observation",
        )
    )


def _refs(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value) for value in values if str(value)))


__all__ = [
    "ConsolidationCluster",
    "ConsolidationConflict",
    "ConsolidationPolicy",
    "ConsolidationReport",
    "ConsolidationSuggestion",
    "DeprecationProposal",
    "EvidenceAge",
    "EvidenceAgeRecord",
    "EvolutionConsolidator",
]
