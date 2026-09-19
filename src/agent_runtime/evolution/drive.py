"""Evidence-aware evolution drive for Runtime-hosted agents.

Evolution is not arbitrary self-modification. The engine turns concrete
frictions into patterns, patterns into needs, and needs into testable
hypotheses. It also permits low-confidence reflective insights that have no
proximate triggering failure, while keeping adoption evidence-bound.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import hashlib
import json
from math import isfinite
from typing import Any, Mapping


class EvolutionFactKind(str, Enum):
    EXTERNAL_MISSION = "EXTERNAL_MISSION"
    CAPABILITY_GAP = "CAPABILITY_GAP"
    FAILURE = "FAILURE"
    HUMAN_DEPENDENCY = "HUMAN_DEPENDENCY"
    RESOURCE_FRICTION = "RESOURCE_FRICTION"
    RESOURCE_PRESSURE = "RESOURCE_PRESSURE"
    REPEATED_INEFFICIENCY = "REPEATED_INEFFICIENCY"
    OPPORTUNITY = "OPPORTUNITY"
    CURIOSITY = "CURIOSITY"
    VALUE_CONFLICT = "VALUE_CONFLICT"
    META_LEARNING_SIGNAL = "META_LEARNING_SIGNAL"
    STAGNATION = "STAGNATION"
    OBSERVATION = "OBSERVATION"


class EvolutionNeedSource(str, Enum):
    EXTERNAL_MISSION = "EXTERNAL_MISSION"
    FAILURE = "FAILURE"
    RESOURCE_PRESSURE = "RESOURCE_PRESSURE"
    CAPABILITY_GAP = "CAPABILITY_GAP"
    REPEATED_INEFFICIENCY = "REPEATED_INEFFICIENCY"
    OPPORTUNITY = "OPPORTUNITY"
    CURIOSITY = "CURIOSITY"
    VALUE_CONFLICT = "VALUE_CONFLICT"
    META_LEARNING_SIGNAL = "META_LEARNING_SIGNAL"


class EvolutionOrigin(str, Enum):
    INDUCTIVE = "INDUCTIVE"
    REFLECTIVE = "REFLECTIVE"
class EvolutionNeedStatus(str, Enum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    REJECTED = "REJECTED"


class EvolutionHypothesisStatus(str, Enum):
    PROPOSED = "PROPOSED"
    SUPPORTED = "SUPPORTED"
    REJECTED = "REJECTED"
    INCONCLUSIVE = "INCONCLUSIVE"
    ADOPTED = "ADOPTED"


@dataclass(frozen=True)
class EvolutionFact:
    fact_id: str
    cycle: int
    observed_at: datetime
    kind: EvolutionFactKind
    subject: str
    summary: str
    severity: float
    recurrence_key: str
    evidence_refs: tuple[str, ...] = ()
    need_hint: str | None = None
    target_capability: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvolutionPattern:
    pattern_id: str
    recurrence_key: str
    kind: EvolutionFactKind
    subject: str
    first_cycle: int
    last_cycle: int
    count: int = 0
    accumulated_severity: float = 0.0
    peak_severity: float = 0.0
    fact_refs: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    need_hint: str | None = None
    target_capability: str | None = None
@dataclass
class EvolutionNeed:
    need_id: str
    key: str
    statement: str
    origin: EvolutionOrigin
    created_cycle: int
    pressure: float
    evidence_refs: list[str] = field(default_factory=list)
    source_pattern_refs: list[str] = field(default_factory=list)
    source_kinds: list[EvolutionNeedSource] = field(default_factory=list)
    abstraction_chain: list[str] = field(default_factory=list)
    target_capability: str | None = None
    status: EvolutionNeedStatus = EvolutionNeedStatus.OPEN
    resolved_cycle: int | None = None


@dataclass
class EvolutionHypothesis:
    hypothesis_id: str
    need_id: str | None
    statement: str
    proposed_change: str
    origin: EvolutionOrigin
    created_cycle: int
    confidence: float
    expected_fitness: dict[str, float] = field(default_factory=dict)
    evidence_refs: list[str] = field(default_factory=list)
    status: EvolutionHypothesisStatus = EvolutionHypothesisStatus.PROPOSED


@dataclass
class EvolutionTrial:
    trial_id: str
    hypothesis_id: str
    cycle: int
    fitness_before: dict[str, float]
    fitness_after: dict[str, float]
    improved_dimensions: list[str]
    regressed_dimensions: list[str]
    evidence_refs: list[str]
    status: EvolutionHypothesisStatus
@dataclass
class EvolutionState:
    facts: list[EvolutionFact] = field(default_factory=list)
    patterns: list[EvolutionPattern] = field(default_factory=list)
    needs: list[EvolutionNeed] = field(default_factory=list)
    hypotheses: list[EvolutionHypothesis] = field(default_factory=list)
    trials: list[EvolutionTrial] = field(default_factory=list)
    last_reflection_cycle: int = 0
    last_improvement_cycle: int = 0


class EvolutionEngine:
    """Bottom-up self-improvement without granting new authority."""

    def __init__(
        self,
        state: EvolutionState | None = None,
        *,
        induction_count: int = 2,
        severe_single_fact: float = 0.75,
        reflection_interval: int = 24,
        stagnation_cycles: int = 72,
    ) -> None:
        if induction_count < 1:
            raise ValueError("induction_count must be positive")
        if reflection_interval < 1 or stagnation_cycles < 1:
            raise ValueError("evolution intervals must be positive")
        self.state = state or EvolutionState()
        self.induction_count = induction_count
        self.severe_single_fact = severe_single_fact
        self.reflection_interval = reflection_interval
        self.stagnation_cycles = stagnation_cycles

    def observe_fact(
        self,
        *,
        cycle: int,
        observed_at: datetime,
        kind: EvolutionFactKind | str,
        subject: str,
        summary: str,
        severity: float,
        evidence_refs: list[str] | tuple[str, ...] = (),
        recurrence_key: str | None = None,
        need_hint: str | None = None,
        target_capability: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> EvolutionFact:
        normalized_kind = (
            kind if isinstance(kind, EvolutionFactKind) else EvolutionFactKind(str(kind))
        )
        subject = str(subject).strip()
        summary = str(summary).strip()
        if not subject or not summary:
            raise ValueError("evolution facts require subject and summary")
        severity = float(severity)
        if not isfinite(severity) or not 0.0 <= severity <= 1.0:
            raise ValueError("evolution fact severity must be within 0..1")
        refs = tuple(dict.fromkeys(str(ref) for ref in evidence_refs if str(ref)))
        key = str(recurrence_key or f"{normalized_kind.value}:{subject}").strip()
        if not key:
            raise ValueError("evolution recurrence_key must be non-empty")
        fact_id = _stable_ref(
            "evf", cycle, normalized_kind.value, key, summary, list(refs)
        )
        existing = next(
            (item for item in self.state.facts if item.fact_id == fact_id), None
        )
        if existing is not None:
            return existing
        fact = EvolutionFact(
            fact_id=fact_id,
            cycle=int(cycle),
            observed_at=observed_at,
            kind=normalized_kind,
            subject=subject,
            summary=summary,
            severity=severity,
            recurrence_key=key,
            evidence_refs=refs,
            need_hint=_clean_optional(need_hint),
            target_capability=_clean_optional(target_capability),
            metadata=dict(metadata or {}),
        )
        self.state.facts.append(fact)
        self.state.facts = self.state.facts[-500:]
        pattern = self._update_pattern(fact)
        self._maybe_induce(pattern, cycle=int(cycle), origin=EvolutionOrigin.INDUCTIVE)
        return fact
    def reflect(
        self,
        *,
        cycle: int,
        observed_at: datetime,
        evidence_refs: list[str] | tuple[str, ...] = (),
    ) -> list[EvolutionNeed]:
        """Low-frequency reflection may surface latent needs without a fresh failure."""
        cycle = int(cycle)
        if cycle - self.state.last_reflection_cycle < self.reflection_interval:
            return []
        self.state.last_reflection_cycle = cycle
        before = {item.need_id for item in self.state.needs}

        unresolved_patterns = [
            pattern
            for pattern in self.state.patterns
            if not self._has_open_need_for_pattern(pattern.pattern_id)
        ]
        unresolved_patterns.sort(
            key=lambda item: (
                item.peak_severity + min(1.0, item.accumulated_severity / 3.0),
                item.count,
                item.last_cycle,
            ),
            reverse=True,
        )
        if unresolved_patterns:
            self._maybe_induce(
                unresolved_patterns[0],
                cycle=cycle,
                origin=EvolutionOrigin.REFLECTIVE,
                force=True,
            )
        elif cycle - self.state.last_improvement_cycle >= self.stagnation_cycles:
            fact = self.observe_fact(
                cycle=cycle,
                observed_at=observed_at,
                kind=EvolutionFactKind.STAGNATION,
                subject="self-development",
                summary=(
                    "No verified evolutionary improvement has been recorded for "
                    f"{cycle - self.state.last_improvement_cycle} cycles"
                ),
                severity=min(
                    1.0,
                    (cycle - self.state.last_improvement_cycle)
                    / (self.stagnation_cycles * 2),
                ),
                evidence_refs=evidence_refs,
                recurrence_key="STAGNATION:self-development",
                need_hint="Explore one evidence-backed improvement to the agent itself",
            )
            pattern = self._pattern(fact.recurrence_key)
            if pattern is not None:
                self._maybe_induce(
                    pattern,
                    cycle=cycle,
                    origin=EvolutionOrigin.REFLECTIVE,
                    force=True,
                )
        return [
            item for item in self.state.needs if item.need_id not in before
        ]
    def propose_insight(
        self,
        *,
        cycle: int,
        statement: str,
        proposed_change: str,
        evidence_refs: list[str] | tuple[str, ...] = (),
        need_id: str | None = None,
        expected_fitness: Mapping[str, float] | None = None,
        confidence: float = 0.35,
    ) -> EvolutionHypothesis:
        """Record a reflective 'aha'. With no evidence it stays low confidence."""
        statement = str(statement).strip()
        proposed_change = str(proposed_change).strip()
        if not statement or not proposed_change:
            raise ValueError("reflective insight requires statement and proposed_change")
        refs = list(dict.fromkeys(str(ref) for ref in evidence_refs if str(ref)))
        if need_id is not None and not any(
            item.need_id == need_id for item in self.state.needs
        ):
            raise KeyError(need_id)
        confidence = float(confidence)
        if not isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise ValueError("insight confidence must be within 0..1")
        if not refs:
            confidence = min(confidence, 0.35)
        fitness = _fitness(expected_fitness or {})
        hypothesis = EvolutionHypothesis(
            hypothesis_id=_stable_ref(
                "evh", cycle, need_id or "", statement, proposed_change, refs
            ),
            need_id=need_id,
            statement=statement,
            proposed_change=proposed_change,
            origin=EvolutionOrigin.REFLECTIVE,
            created_cycle=int(cycle),
            confidence=confidence,
            expected_fitness=fitness,
            evidence_refs=refs,
        )
        existing = next(
            (
                item
                for item in self.state.hypotheses
                if item.hypothesis_id == hypothesis.hypothesis_id
            ),
            None,
        )
        if existing is not None:
            return existing
        self.state.hypotheses.append(hypothesis)
        return hypothesis
    def propose_for_need(
        self,
        need_id: str,
        *,
        cycle: int,
        proposed_change: str,
        expected_fitness: Mapping[str, float] | None = None,
        confidence: float = 0.5,
    ) -> EvolutionHypothesis:
        need = self._need(need_id)
        return self.propose_insight(
            cycle=cycle,
            statement=f"If I address '{need.statement}', my fitness should improve",
            proposed_change=proposed_change,
            evidence_refs=need.evidence_refs,
            need_id=need.need_id,
            expected_fitness=expected_fitness,
            confidence=confidence,
        )

    def evaluate_trial(
        self,
        hypothesis_id: str,
        *,
        cycle: int,
        fitness_before: Mapping[str, float],
        fitness_after: Mapping[str, float],
        evidence_refs: list[str] | tuple[str, ...],
        tolerance: float = 1e-9,
    ) -> EvolutionTrial:
        hypothesis = self._hypothesis(hypothesis_id)
        refs = list(dict.fromkeys(str(ref) for ref in evidence_refs if str(ref)))
        if not refs:
            raise ValueError("evolution trial evaluation requires evidence")
        before = _fitness(fitness_before)
        after = _fitness(fitness_after)
        if set(before) != set(after) or not before:
            raise ValueError("fitness before/after must have the same non-empty dimensions")
        improved = [
            name for name in before if after[name] > before[name] + tolerance
        ]
        regressed = [
            name for name in before if after[name] < before[name] - tolerance
        ]
        if improved and not regressed:
            status = EvolutionHypothesisStatus.SUPPORTED
        elif regressed and not improved:
            status = EvolutionHypothesisStatus.REJECTED
        else:
            status = EvolutionHypothesisStatus.INCONCLUSIVE
        trial = EvolutionTrial(
            trial_id=_stable_ref(
                "evt", hypothesis_id, cycle, before, after, refs
            ),
            hypothesis_id=hypothesis_id,
            cycle=int(cycle),
            fitness_before=before,
            fitness_after=after,
            improved_dimensions=improved,
            regressed_dimensions=regressed,
            evidence_refs=refs,
            status=status,
        )
        if not any(item.trial_id == trial.trial_id for item in self.state.trials):
            self.state.trials.append(trial)
        hypothesis.status = status
        if status == EvolutionHypothesisStatus.SUPPORTED:
            hypothesis.confidence = max(hypothesis.confidence, 0.8)
            self.state.last_improvement_cycle = max(
                self.state.last_improvement_cycle, int(cycle)
            )
        elif status == EvolutionHypothesisStatus.REJECTED:
            hypothesis.confidence = min(hypothesis.confidence, 0.2)
        return trial

    def adopt(self, hypothesis_id: str, *, cycle: int) -> EvolutionHypothesis:
        hypothesis = self._hypothesis(hypothesis_id)
        if hypothesis.status != EvolutionHypothesisStatus.SUPPORTED:
            raise PermissionError("only evidence-supported evolution may be adopted")
        hypothesis.status = EvolutionHypothesisStatus.ADOPTED
        self.state.last_improvement_cycle = max(
            self.state.last_improvement_cycle, int(cycle)
        )
        if hypothesis.need_id is not None:
            self.resolve_need(hypothesis.need_id, cycle=cycle)
        return hypothesis

    def resolve_need(self, need_id: str, *, cycle: int) -> EvolutionNeed:
        need = self._need(need_id)
        need.status = EvolutionNeedStatus.RESOLVED
        need.resolved_cycle = int(cycle)
        return need

    def reject_need(self, need_id: str, *, cycle: int) -> EvolutionNeed:
        need = self._need(need_id)
        need.status = EvolutionNeedStatus.REJECTED
        need.resolved_cycle = int(cycle)
        return need

    def resolve_target_capability(
        self, capability_id: str, *, cycle: int
    ) -> list[EvolutionNeed]:
        resolved: list[EvolutionNeed] = []
        for need in self.state.needs:
            if (
                need.status == EvolutionNeedStatus.OPEN
                and need.target_capability == capability_id
            ):
                self.resolve_need(need.need_id, cycle=cycle)
                resolved.append(need)
        if resolved:
            self.state.last_improvement_cycle = max(
                self.state.last_improvement_cycle, int(cycle)
            )
        return resolved

    def pressure(self, cycle: int) -> float:
        open_needs = [
            item.pressure
            for item in self.state.needs
            if item.status == EvolutionNeedStatus.OPEN
        ]
        need_pressure = max(open_needs, default=0.0)
        stagnation = max(0, int(cycle) - self.state.last_improvement_cycle)
        stagnation_pressure = min(
            0.6, stagnation / max(1, self.stagnation_cycles * 2)
        )
        unresolved_pattern_pressure = max(
            (
                min(
                    0.7,
                    0.12 * pattern.count
                    + 0.35 * pattern.peak_severity
                    + 0.08 * min(3.0, pattern.accumulated_severity),
                )
                for pattern in self.state.patterns
                if not self._has_resolved_need_for_pattern(pattern.pattern_id)
            ),
            default=0.0,
        )
        return min(1.0, max(need_pressure, stagnation_pressure, unresolved_pattern_pressure))
    def open_needs(self) -> list[EvolutionNeed]:
        return [
            item for item in self.state.needs
            if item.status == EvolutionNeedStatus.OPEN
        ]

    def _update_pattern(self, fact: EvolutionFact) -> EvolutionPattern:
        pattern = self._pattern(fact.recurrence_key)
        if pattern is None:
            pattern = EvolutionPattern(
                pattern_id=_stable_ref(
                    "evp", fact.kind.value, fact.recurrence_key
                ),
                recurrence_key=fact.recurrence_key,
                kind=fact.kind,
                subject=fact.subject,
                first_cycle=fact.cycle,
                last_cycle=fact.cycle,
            )
            self.state.patterns.append(pattern)
        pattern.last_cycle = max(pattern.last_cycle, fact.cycle)
        if fact.fact_id not in pattern.fact_refs:
            pattern.fact_refs.append(fact.fact_id)
            pattern.count += 1
            pattern.accumulated_severity += fact.severity
            pattern.peak_severity = max(pattern.peak_severity, fact.severity)
        pattern.evidence_refs = list(
            dict.fromkeys([*pattern.evidence_refs, *fact.evidence_refs])
        )[-64:]
        pattern.need_hint = fact.need_hint or pattern.need_hint
        pattern.target_capability = (
            fact.target_capability or pattern.target_capability
        )
        return pattern

    def _maybe_induce(
        self,
        pattern: EvolutionPattern,
        *,
        cycle: int,
        origin: EvolutionOrigin,
        force: bool = False,
    ) -> EvolutionNeed | None:
        existing = next(
            (
                item
                for item in self.state.needs
                if pattern.pattern_id in item.source_pattern_refs
                and item.status == EvolutionNeedStatus.OPEN
            ),
            None,
        )
        if existing is not None:
            existing.pressure = max(
                existing.pressure, self._pattern_pressure(pattern)
            )
            existing.evidence_refs = list(
                dict.fromkeys([*existing.evidence_refs, *pattern.evidence_refs])
            )
            source = _need_source_for_kind(pattern.kind)
            if source is not None and source not in existing.source_kinds:
                existing.source_kinds.append(source)
            return existing
        if not force and (
            pattern.count < self.induction_count
            and pattern.peak_severity < self.severe_single_fact
        ):
            return None
        statement = pattern.need_hint or self._default_need_statement(pattern)
        key = _need_key(pattern, statement)
        duplicate = next(
            (
                item
                for item in self.state.needs
                if item.key == key and item.status == EvolutionNeedStatus.OPEN
            ),
            None,
        )
        if duplicate is not None:
            if pattern.pattern_id not in duplicate.source_pattern_refs:
                duplicate.source_pattern_refs.append(pattern.pattern_id)
            duplicate.evidence_refs = list(
                dict.fromkeys([*duplicate.evidence_refs, *pattern.evidence_refs])
            )
            duplicate.pressure = max(
                duplicate.pressure, self._pattern_pressure(pattern)
            )
            source = _need_source_for_kind(pattern.kind)
            if source is not None and source not in duplicate.source_kinds:
                duplicate.source_kinds.append(source)
            return duplicate
        chain = [
            f"fact: {pattern.subject}",
            (
                "pattern: "
                f"{pattern.recurrence_key} observed {pattern.count} time(s), "
                f"peak severity={pattern.peak_severity:.2f}"
            ),
            f"need: {statement}",
        ]
        need = EvolutionNeed(
            need_id=_stable_ref("evn", key, cycle),
            key=key,
            statement=statement,
            origin=origin,
            created_cycle=int(cycle),
            pressure=self._pattern_pressure(pattern),
            evidence_refs=list(pattern.evidence_refs),
            source_pattern_refs=[pattern.pattern_id],
            source_kinds=(
                []
                if _need_source_for_kind(pattern.kind) is None
                else [_need_source_for_kind(pattern.kind)]
            ),
            abstraction_chain=chain,
            target_capability=pattern.target_capability,
        )
        self.state.needs.append(need)
        return need

    @staticmethod
    def _default_need_statement(pattern: EvolutionPattern) -> str:
        if pattern.kind == EvolutionFactKind.CAPABILITY_GAP:
            target = pattern.target_capability or pattern.subject
            return f"Acquire or develop capability '{target}'"
        if pattern.kind == EvolutionFactKind.HUMAN_DEPENDENCY:
            return f"Reduce recurring human dependency around '{pattern.subject}'"
        if pattern.kind in {
            EvolutionFactKind.RESOURCE_FRICTION,
            EvolutionFactKind.RESOURCE_PRESSURE,
        }:
            return f"Improve resource efficiency or resilience for '{pattern.subject}'"
        if pattern.kind == EvolutionFactKind.FAILURE:
            return f"Improve reliability of '{pattern.subject}'"
        if pattern.kind == EvolutionFactKind.EXTERNAL_MISSION:
            return f"Improve capability to advance external mission '{pattern.subject}'"
        if pattern.kind == EvolutionFactKind.REPEATED_INEFFICIENCY:
            return f"Reduce repeated inefficiency around '{pattern.subject}'"
        if pattern.kind == EvolutionFactKind.OPPORTUNITY:
            return f"Evaluate and exploit evidence-backed opportunity '{pattern.subject}'"
        if pattern.kind == EvolutionFactKind.CURIOSITY:
            return f"Investigate curiosity-driven capability opportunity '{pattern.subject}'"
        if pattern.kind == EvolutionFactKind.VALUE_CONFLICT:
            return f"Resolve recurring value conflict around '{pattern.subject}'"
        if pattern.kind in {
            EvolutionFactKind.META_LEARNING_SIGNAL,
            EvolutionFactKind.STAGNATION,
        }:
            return "Explore one evidence-backed improvement to the learning process"
        return f"Investigate whether '{pattern.subject}' reveals a structural self-improvement need"

    @staticmethod
    def _pattern_pressure(pattern: EvolutionPattern) -> float:
        return min(
            1.0,
            0.20
            + 0.12 * min(4, pattern.count)
            + 0.35 * pattern.peak_severity
            + 0.05 * min(3.0, pattern.accumulated_severity),
        )
    def _pattern(self, recurrence_key: str) -> EvolutionPattern | None:
        return next(
            (
                item
                for item in self.state.patterns
                if item.recurrence_key == recurrence_key
            ),
            None,
        )

    def _need(self, need_id: str) -> EvolutionNeed:
        item = next(
            (item for item in self.state.needs if item.need_id == need_id), None
        )
        if item is None:
            raise KeyError(need_id)
        return item

    def _hypothesis(self, hypothesis_id: str) -> EvolutionHypothesis:
        item = next(
            (
                item
                for item in self.state.hypotheses
                if item.hypothesis_id == hypothesis_id
            ),
            None,
        )
        if item is None:
            raise KeyError(hypothesis_id)
        return item

    def _has_open_need_for_pattern(self, pattern_id: str) -> bool:
        return any(
            pattern_id in item.source_pattern_refs
            and item.status == EvolutionNeedStatus.OPEN
            for item in self.state.needs
        )

    def _has_resolved_need_for_pattern(self, pattern_id: str) -> bool:
        return any(
            pattern_id in item.source_pattern_refs
            and item.status == EvolutionNeedStatus.RESOLVED
            for item in self.state.needs
        )


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _fitness(values: Mapping[str, float]) -> dict[str, float]:
    normalized: dict[str, float] = {}
    for key, value in values.items():
        name = str(key).strip()
        number = float(value)
        if not name or not isfinite(number):
            raise ValueError("fitness dimensions require finite numeric values")
        normalized[name] = number
    return normalized


def _need_source_for_kind(kind: EvolutionFactKind) -> EvolutionNeedSource | None:
    mapping = {
        EvolutionFactKind.EXTERNAL_MISSION: EvolutionNeedSource.EXTERNAL_MISSION,
        EvolutionFactKind.FAILURE: EvolutionNeedSource.FAILURE,
        EvolutionFactKind.RESOURCE_FRICTION: EvolutionNeedSource.RESOURCE_PRESSURE,
        EvolutionFactKind.RESOURCE_PRESSURE: EvolutionNeedSource.RESOURCE_PRESSURE,
        EvolutionFactKind.CAPABILITY_GAP: EvolutionNeedSource.CAPABILITY_GAP,
        EvolutionFactKind.HUMAN_DEPENDENCY: EvolutionNeedSource.CAPABILITY_GAP,
        EvolutionFactKind.REPEATED_INEFFICIENCY: EvolutionNeedSource.REPEATED_INEFFICIENCY,
        EvolutionFactKind.OPPORTUNITY: EvolutionNeedSource.OPPORTUNITY,
        EvolutionFactKind.CURIOSITY: EvolutionNeedSource.CURIOSITY,
        EvolutionFactKind.VALUE_CONFLICT: EvolutionNeedSource.VALUE_CONFLICT,
        EvolutionFactKind.META_LEARNING_SIGNAL: EvolutionNeedSource.META_LEARNING_SIGNAL,
        EvolutionFactKind.STAGNATION: EvolutionNeedSource.META_LEARNING_SIGNAL,
    }
    return mapping.get(kind)


def _need_key(pattern: EvolutionPattern, statement: str) -> str:
    target = pattern.target_capability or pattern.subject
    source = _need_source_for_kind(pattern.kind)
    source_key = pattern.kind.value if source is None else source.value
    return hashlib.sha256(
        f"{source_key}|{target}|{statement}".encode("utf-8")
    ).hexdigest()[:24]


def _stable_ref(prefix: str, *parts: Any) -> str:
    encoded = json.dumps(
        parts,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    ).encode("utf-8")
    return f"{prefix}:{hashlib.sha256(encoded).hexdigest()}"


__all__ = [
    "EvolutionEngine",
    "EvolutionFact",
    "EvolutionFactKind",
    "EvolutionHypothesis",
    "EvolutionHypothesisStatus",
    "EvolutionNeed",
    "EvolutionNeedSource",
    "EvolutionNeedStatus",
    "EvolutionOrigin",
    "EvolutionPattern",
    "EvolutionState",
    "EvolutionTrial",
]
