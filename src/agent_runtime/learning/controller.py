"""Learning controller: validate proposals, assign credit, never raise authorization."""
from __future__ import annotations

from dataclasses import dataclass

from ..clock import new_id
from ..experience.trace import ExperienceTrace
from ..governance.autonomy import AutonomyPolicy
from ..records import OutcomeRecord
from ..self_model.model import SelfModel
from .credit import CreditAssignment
from .experience import Hypothesis, LessonCandidate, StrategyEvaluation, StrategyVersion
from .habit import HabitBinding
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


@dataclass
class CommitResult:
    accepted: bool
    reason: str = ""
    new_proficiency: float | None = None


class StrategyRegistry:
    def __init__(self, versions: list[StrategyVersion] | None = None) -> None:
        self.versions: list[StrategyVersion] = list(versions or [])

    def commit(self, version: StrategyVersion) -> StrategyVersion:
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
    ) -> None:
        self.hypotheses: list[Hypothesis] = list(hypotheses or [])
        self.lesson_candidates: list[LessonCandidate] = list(lesson_candidates or [])
        self.strategies = StrategyRegistry(strategy_versions)
        self.habit_bindings: list[HabitBinding] = list(habit_bindings or [])
        self.credit_assignments: list[CreditAssignment] = list(credit_assignments or [])

    def assign_credit(
        self, outcome: OutcomeRecord, traces: list[ExperienceTrace]
    ) -> CreditAssignment:
        matched = next(
            (trace for trace in traces if outcome.action_id in trace.action_refs),
            None,
        )
        if matched is None:
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

    def observe(self, *, traces: list[ExperienceTrace], outcomes: list[OutcomeRecord]) -> None:
        for outcome in outcomes:
            self.assign_credit(outcome, traces)

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
        _ = (body, evidence_refs)
        if failures >= 1 and successes < 3:
            return CommitResult(accepted=False, reason="single failure does not rewrite strategy")
        return CommitResult(accepted=True, reason="ok")

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
    ) -> CommitResult:
        _ = learning_signal
        lowered = path.lower()
        if any(token in lowered for token in _AUTH_PATHS):
            return CommitResult(accepted=False, reason="learning cannot raise authorization")
        if autonomy is not None and path.startswith("autonomy"):
            return CommitResult(accepted=False, reason="learning cannot raise authorization")
        if self_model is not None:
            self_model.apply_update(path, new_value, reason, uncertainty, evidence_refs)
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
