"""Learning controller: validate proposals, assign credit, never raise authorization."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import isfinite

from ..clock import new_id
from ..experience.refs import stable_ref
from ..experience.trace import ExperienceTrace
from ..governance.autonomy import AutonomyPolicy
from ..records import ActionRecord, DecisionRecord, OutcomeRecord
from ..self_model.model import SelfModel
from .credit import CreditAssignment
from .evidence import VerifiedLearningEvidence
from .experience import Hypothesis, LessonCandidate, StrategyEvaluation, StrategyVersion
from .habit import HabitBinding
from .judgment import JudgmentSettlement, relevant_judgments, settle_judgment
from .social import SocialLessonProvenanceError


_AUTH_PATHS = (
    "autonomy",
    "constitution",
    "allowed_actions",
    "authorization",
    "governance",
    "budget",
    "may_spawn",
)
_SELF_MODEL_PREFIXES = frozenset(
    {"capabilities", "resources", "internal_state", "vulnerabilities"}
)


class StrategyGovernanceError(ValueError):
    """Raised when a strategy is activated without LearningController validation."""


@dataclass
class CommitResult:
    accepted: bool
    reason: str = ""
    new_proficiency: float | None = None


class StrategyRegistry:
    def __init__(self, versions: list[StrategyVersion] | None = None) -> None:
        self.versions: list[StrategyVersion] = list(versions or [])

    def commit(self, version: StrategyVersion) -> StrategyVersion:
        raise StrategyGovernanceError("use LearningController.commit_strategy")

    def _install(self, version: StrategyVersion) -> StrategyVersion:
        if any(
            item.strategy_id == version.strategy_id and item.version == version.version
            for item in self.versions
        ):
            raise StrategyGovernanceError("strategy version already exists")
        for item in self.versions:
            if item.strategy_id == version.strategy_id:
                item.active = False
        version.active = True
        self.versions.append(version)
        return version

    def active(self, strategy_id: str) -> StrategyVersion:
        for item in reversed(self.versions):
            if item.strategy_id == strategy_id and item.active:
                return item
        raise KeyError(strategy_id)

    def replay(self, strategy_id: str, version: str) -> str:
        for item in self.versions:
            if item.strategy_id == strategy_id and item.version == version:
                return item.body
        raise KeyError((strategy_id, version))

    def compare(
        self, strategy_id: str, left: str, right: str
    ) -> tuple[StrategyEvaluation, StrategyEvaluation]:
        return (
            StrategyEvaluation(
                strategy_id=strategy_id,
                score=1.0 if self.active(strategy_id).version == left else 0.5,
                notes=self.replay(strategy_id, left),
                version=left,
            ),
            StrategyEvaluation(
                strategy_id=strategy_id,
                score=1.0 if self.active(strategy_id).version == right else 0.5,
                notes=self.replay(strategy_id, right),
                version=right,
            ),
        )

    def rollback(self, strategy_id: str) -> StrategyVersion:
        current = self.active(strategy_id)
        current.active = False
        prior = [
            item
            for item in self.versions
            if item.strategy_id == strategy_id and item.version != current.version
        ]
        if not prior:
            current.active = True
            return current
        prior[-1].active = True
        return prior[-1]


class LearningController:
    def __init__(
        self,
        *,
        hypotheses: list[Hypothesis] | None = None,
        lesson_candidates: list[LessonCandidate] | None = None,
        strategy_versions: list[StrategyVersion] | None = None,
        habit_bindings: list[HabitBinding] | None = None,
        credit_assignments: list[CreditAssignment] | None = None,
        verified_evidence: list[VerifiedLearningEvidence] | None = None,
        judgment_settlements: list[JudgmentSettlement] | None = None,
        agent_id: str | None = None,
    ) -> None:
        self.agent_id = agent_id
        self.hypotheses: list[Hypothesis] = list(hypotheses or [])
        self.lesson_candidates: list[LessonCandidate] = list(lesson_candidates or [])
        self.strategies = StrategyRegistry(strategy_versions)
        self.habit_bindings: list[HabitBinding] = list(habit_bindings or [])
        self.credit_assignments: list[CreditAssignment] = list(credit_assignments or [])
        self.verified_evidence: list[VerifiedLearningEvidence] = list(
            verified_evidence or []
        )
        self.judgment_settlements: list[JudgmentSettlement] = list(
            judgment_settlements or []
        )
        if self.agent_id is not None and any(
            item.agent_id != self.agent_id for item in self.verified_evidence
        ):
            raise ValueError("verified evidence belongs to another agent")

    def assign_credit(
        self, outcome: OutcomeRecord, traces: list[ExperienceTrace]
    ) -> CreditAssignment:
        existing = next(
            (
                item
                for item in self.credit_assignments
                if item.outcome_id == outcome.outcome_id
                and item.action_id == outcome.action_id
            ),
            None,
        )
        if existing is not None:
            return existing
        matches = [
            trace for trace in traces if outcome.action_id in trace.action_refs
        ]
        if len(matches) != 1:
            credit = CreditAssignment(
                outcome_id=outcome.outcome_id,
                action_id=outcome.action_id,
                trace_id="",
                plan_ref=None,
                step_ref=None,
                delayed=True,
            )
            self.credit_assignments.append(credit)
            return credit
        matched = matches[0]
        delayed = outcome.timestamp > matched.observed_at
        credit = CreditAssignment(
            outcome_id=outcome.outcome_id,
            action_id=outcome.action_id,
            trace_id=matched.trace_id,
            plan_ref=matched.plan_ref,
            step_ref=matched.step_ref,
            delayed=delayed,
        )
        self.credit_assignments.append(credit)
        return credit

    def observe(
        self,
        *,
        traces: list[ExperienceTrace],
        outcomes: list[OutcomeRecord],
    ) -> list[VerifiedLearningEvidence]:
        created: list[VerifiedLearningEvidence] = []
        for outcome in outcomes:
            credit = self.assign_credit(outcome, traces)
            trace = next(
                (item for item in traces if item.trace_id == credit.trace_id),
                None,
            )
            evidence = self._verify(trace, outcome, credit)
            if evidence is None:
                continue
            if any(
                item.evidence_id == evidence.evidence_id
                or item.outcome_id == evidence.outcome_id
                for item in self.verified_evidence
            ):
                continue
            self.verified_evidence.append(evidence)
            created.append(evidence)
        return created

    def settle_decision(
        self,
        *,
        decision: DecisionRecord,
        actions: list[ActionRecord],
        outcomes: list[OutcomeRecord],
    ) -> JudgmentSettlement:
        settlement = settle_judgment(
            decision,
            actions=actions,
            outcomes=outcomes,
        )
        for index, existing in enumerate(self.judgment_settlements):
            if existing.decision_id != decision.decision_id:
                continue
            if (
                existing.status != "UNRESOLVED"
                and settlement.status == "UNRESOLVED"
            ):
                return existing
            if existing == settlement:
                return existing
            self.judgment_settlements[index] = settlement
            return settlement
        self.judgment_settlements.append(settlement)
        return settlement

    def relevant_judgments(
        self,
        query: str,
        *,
        decisions: list[DecisionRecord],
        limit: int = 4,
    ) -> list[dict]:
        return relevant_judgments(
            query,
            decisions=decisions,
            settlements=self.judgment_settlements,
            limit=limit,
        )

    def _verify(
        self,
        trace: ExperienceTrace | None,
        outcome: OutcomeRecord,
        credit: CreditAssignment,
    ) -> VerifiedLearningEvidence | None:
        if trace is None or not trace.agent_id or not trace.trace_id:
            return None
        if self.agent_id is not None and trace.agent_id != self.agent_id:
            return None
        if outcome.action_id not in trace.action_refs:
            return None
        if outcome.outcome_id not in trace.outcome_refs:
            return None
        if outcome.timestamp < trace.effective_at:
            return None
        if outcome.evidence_result in {"UNVERIFIABLE", "UNCOVERED"}:
            return None
        if not isfinite(trace.confidence) or not 0.5 <= trace.confidence <= 1.0:
            return None
        refs = _unique_refs(
            [
                trace.trace_id,
                outcome.outcome_id,
                outcome.action_id,
                *trace.evidence_refs,
                *trace.caused_by,
            ]
        )
        return VerifiedLearningEvidence(
            evidence_id=stable_ref(
                "vle",
                trace.agent_id,
                trace.trace_id,
                outcome.outcome_id,
                outcome.action_id,
            ),
            agent_id=trace.agent_id,
            trace_id=trace.trace_id,
            outcome_id=outcome.outcome_id,
            action_id=outcome.action_id,
            goal_refs=list(trace.goal_refs),
            plan_ref=trace.plan_ref,
            step_ref=trace.step_ref,
            success=outcome.success,
            delayed=credit.delayed,
            verified_at=outcome.timestamp,
            confidence=trace.confidence,
            evidence_refs=refs,
        )

    def hypothesize(
        self,
        statement: str,
        *,
        evidence_refs: list[str],
        confidence: float = 0.5,
        contradicts: list[str] | None = None,
    ) -> Hypothesis:
        item = Hypothesis(
            hypothesis_id=new_id("hyp"),
            statement=statement,
            evidence_refs=list(evidence_refs),
            confidence=confidence,
            contradicts=list(contradicts or []),
        )
        for other in self.hypotheses:
            if other.hypothesis_id in item.contradicts:
                other.confidence = max(0.1, other.confidence - 0.2)
                if other.hypothesis_id not in item.contradicts:
                    continue
        self.hypotheses.append(item)
        return item

    def propose_strategy(
        self,
        *,
        strategy_id: str,
        body: str,
        evidence_refs: list[str],
        successes: int,
        failures: int,
    ) -> CommitResult:
        _ = (strategy_id, body)
        evidence, reason = self._strategy_evidence(evidence_refs)
        if reason:
            return CommitResult(accepted=False, reason=reason)
        actual_successes = sum(item.success for item in evidence)
        actual_failures = len(evidence) - actual_successes
        if successes != actual_successes or failures != actual_failures:
            return CommitResult(accepted=False, reason="claimed result counts do not match evidence")
        if actual_failures >= 1 and actual_successes < 3:
            return CommitResult(accepted=False, reason="single failure does not rewrite strategy")
        if actual_successes < 3:
            return CommitResult(accepted=False, reason="not enough verified successes")
        return CommitResult(accepted=True, reason="ok")

    def commit_strategy(
        self,
        *,
        strategy_id: str,
        version: str,
        body: str,
        evidence_refs: list[str],
        successes: int,
        failures: int,
    ) -> CommitResult:
        if any(
            item.strategy_id == strategy_id and item.version == version
            for item in self.strategies.versions
        ):
            return CommitResult(accepted=False, reason="strategy version already exists")
        result = self.propose_strategy(
            strategy_id=strategy_id,
            body=body,
            evidence_refs=evidence_refs,
            successes=successes,
            failures=failures,
        )
        if not result.accepted:
            return result
        self.strategies._install(
            StrategyVersion(
                strategy_id=strategy_id,
                version=version,
                body=body,
                evidence_refs=list(evidence_refs),
            )
        )
        return result

    def commit_strategy_from_evidence(
        self,
        *,
        strategy_id: str,
        version: str,
        body: str,
        evidence_refs: list[str],
    ) -> CommitResult:
        evidence, reason = self._strategy_evidence(evidence_refs)
        if reason:
            return CommitResult(accepted=False, reason=reason)
        successes = sum(item.success for item in evidence)
        return self.commit_strategy(
            strategy_id=strategy_id,
            version=version,
            body=body,
            evidence_refs=evidence_refs,
            successes=successes,
            failures=len(evidence) - successes,
        )

    def _strategy_evidence(
        self,
        evidence_refs: list[str],
    ) -> tuple[list[VerifiedLearningEvidence], str]:
        requested = list(dict.fromkeys(evidence_refs))
        if not requested:
            return [], "verified evidence is required"
        by_id = {item.evidence_id: item for item in self.verified_evidence}
        if any(ref not in by_id for ref in requested):
            return [], "unverified evidence"
        used = {
            ref
            for version in self.strategies.versions
            for ref in version.evidence_refs
        }
        if any(ref in used for ref in requested):
            return [], "evidence already consumed by a strategy version"
        return [by_id[ref] for ref in requested], ""

    def propose_self_model(
        self,
        *,
        path: str,
        new_value,
        reason: str,
        uncertainty: float,
        evidence_refs: list[str],
        learning_signal: float = 0.0,
        autonomy: AutonomyPolicy | None = None,
        self_model: SelfModel | None = None,
        timestamp: datetime | None = None,
    ) -> CommitResult:
        _ = learning_signal
        lowered = path.lower()
        if any(token in lowered for token in _AUTH_PATHS):
            return CommitResult(accepted=False, reason="learning cannot raise authorization")
        if autonomy is not None and path.startswith("autonomy"):
            return CommitResult(accepted=False, reason="learning cannot raise authorization")
        prefix, separator, name = path.partition(".")
        if not separator or not name or prefix not in _SELF_MODEL_PREFIXES:
            return CommitResult(accepted=False, reason="unsupported self-model path")
        if self_model is not None:
            applied = self_model.apply_update(
                path,
                new_value,
                reason,
                uncertainty,
                evidence_refs,
                timestamp=timestamp,
            )
            if not applied:
                return CommitResult(accepted=False, reason="unsupported self-model path or value")
        return CommitResult(accepted=True, reason="ok")

    def propose_capability_belief(
        self,
        *,
        name: str,
        proficiency: float,
        evidence_refs: list[str],
        current_proficiency: float,
    ) -> CommitResult:
        _ = (name, evidence_refs)
        delta = proficiency - current_proficiency
        if delta > 0.2 and len(evidence_refs) <= 1:
            return CommitResult(
                accepted=False,
                reason="proficiency must change gradually",
                new_proficiency=current_proficiency,
            )
        return CommitResult(
            accepted=True,
            reason="ok",
            new_proficiency=min(proficiency, current_proficiency + 0.1),
        )

    def commit_social_lesson(
        self,
        *,
        claim: str,
        teacher_id: str | None,
        source: str | None,
        provenance: list[str],
    ) -> LessonCandidate:
        if not teacher_id or not source or not provenance:
            raise SocialLessonProvenanceError("teacher, source, and provenance are required")
        lesson = LessonCandidate(
            lesson_id=new_id("lesson"),
            claim=claim,
            support=list(provenance),
            teacher_id=teacher_id,
            source=source,
            provenance=list(provenance),
        )
        self.lesson_candidates.append(lesson)
        return lesson

    def consider_habit(
        self,
        *,
        fragment: str,
        successes: int,
        failures: int,
        risk: str,
        verifiable: bool,
    ) -> HabitBinding | None:
        existing = next(
            (item for item in self.habit_bindings if item.fragment == fragment),
            None,
        )
        bound = (
            successes >= 3
            and failures == 0
            and risk == "low"
            and verifiable
            and "introspect" not in fragment
            and "noop" not in fragment
        )
        if existing is None:
            existing = HabitBinding(
                habit_id=new_id("habit"),
                fragment=fragment,
                successes=successes,
                bound=bound,
            )
            self.habit_bindings.append(existing)
        else:
            existing.successes = successes
            existing.bound = bound
        if successes < 3:
            existing.bound = False
            return existing
        return existing

    def unbind_habit(self, habit_id: str, *, reason: str) -> HabitBinding:
        _ = reason
        habit = next((item for item in self.habit_bindings if item.habit_id == habit_id), None)
        if habit is None:
            raise KeyError(habit_id)
        habit.bound = False
        return habit


def _unique_refs(refs: list[str]) -> list[str]:
    return list(dict.fromkeys(ref for ref in refs if ref))
