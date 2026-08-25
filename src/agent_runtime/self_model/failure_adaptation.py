"""Evidence-linked behavioral adaptation after material self-prediction error."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..action.proposal import ActionProposal
from ..clock import new_id
from ..records import OutcomeRecord
from .beliefs import SelfPrediction


class PredictionGuardState(str, Enum):
    ACTIVE = "ACTIVE"
    PROBATION = "PROBATION"
    RECOVERED = "RECOVERED"
    EXPIRED = "EXPIRED"


@dataclass
class PredictionFailureGuard:
    guard_id: str
    action_kind: str
    target_path: str
    state: PredictionGuardState
    created_cycle: int
    updated_cycle: int
    failure_count: int
    last_error: float
    last_confidence: float
    failure_prediction_ids: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    risk_domains: set[str] = field(default_factory=set)
    diagnostic_outcome_id: str | None = None
    recovered_prediction_id: str | None = None
    expires_cycle: int | None = None
    anomaly_expected: Any = None
    anomaly_observed: Any = None
    diagnostic_attempts: int = 0
    max_diagnostic_attempts: int = 2
    diagnostic_prediction_ids: list[str] = field(default_factory=list)
    diagnostic_explanation_refs: list[str] = field(default_factory=list)


class PredictionFailureAdapter:
    """Turn prediction error into bounded, reversible action friction."""

    def __init__(
        self,
        guards: list[PredictionFailureGuard] | None = None,
        *,
        error_threshold: float = 0.20,
        minimum_confidence: float = 0.60,
        max_guards: int = 64,
        expiry_cycles: int = 24,
    ) -> None:
        self.guards = list(guards or [])
        self.error_threshold = error_threshold
        self.minimum_confidence = minimum_confidence
        self.max_guards = max_guards
        self.expiry_cycles = expiry_cycles

    def observe_prediction(
        self, prediction: SelfPrediction, *, cycle: int
    ) -> PredictionFailureGuard | None:
        action_kind = str(prediction.intervention.get("action") or "")
        if not action_kind or not prediction.target_path:
            return None
        binding_id = str(
            prediction.intervention.get("prediction_failure_guard_id") or ""
        )
        if binding_id:
            guard = self.guard(binding_id)
            if guard is None:
                return None
            guard.updated_cycle = cycle
            guard.expires_cycle = cycle + self.expiry_cycles
            if prediction.prediction_id not in guard.diagnostic_prediction_ids:
                guard.diagnostic_prediction_ids.append(prediction.prediction_id)
            self._add_evidence(guard, prediction)
            return guard
        if prediction.status == "MATCHED":
            guard = self._find(
                action_kind, prediction.target_path, {PredictionGuardState.PROBATION}
            )
            if guard is not None:
                guard.state = PredictionGuardState.RECOVERED
                guard.updated_cycle = cycle
                guard.recovered_prediction_id = prediction.prediction_id
                self._add_evidence(guard, prediction)
            return guard
        if (
            prediction.status != "VIOLATED"
            or prediction.error is None
            or prediction.error < self.error_threshold
            or prediction.confidence < self.minimum_confidence
        ):
            return None
        guard = self._find(
            action_kind,
            prediction.target_path,
            {PredictionGuardState.ACTIVE, PredictionGuardState.PROBATION},
        )
        if guard is None:
            guard = PredictionFailureGuard(
                guard_id=new_id("pfg"),
                action_kind=action_kind,
                target_path=prediction.target_path,
                state=PredictionGuardState.ACTIVE,
                created_cycle=cycle,
                updated_cycle=cycle,
                failure_count=1,
                last_error=float(prediction.error),
                last_confidence=float(prediction.confidence),
                risk_domains=_prediction_risk_domains(prediction),
                expires_cycle=cycle + self.expiry_cycles,
                anomaly_expected=prediction.predicted.get(prediction.target_path),
                anomaly_observed=prediction.observed,
            )
            self.guards.append(guard)
            if len(self.guards) > self.max_guards:
                self.guards = self.guards[-self.max_guards :]
        else:
            guard.state = PredictionGuardState.ACTIVE
            guard.updated_cycle = cycle
            guard.failure_count += 1
            guard.last_error = float(prediction.error)
            guard.last_confidence = float(prediction.confidence)
            guard.diagnostic_outcome_id = None
            guard.expires_cycle = cycle + self.expiry_cycles
            guard.anomaly_expected = prediction.predicted.get(
                prediction.target_path
            )
            guard.anomaly_observed = prediction.observed
            guard.diagnostic_attempts = 0
            guard.diagnostic_prediction_ids = []
            guard.diagnostic_explanation_refs = []
        self._add_evidence(guard, prediction)
        return guard

    def observe_outcome(
        self,
        proposal: ActionProposal,
        outcome: OutcomeRecord,
        *,
        cycle: int,
        prediction: SelfPrediction | None = None,
    ) -> list[PredictionFailureGuard]:
        if proposal.kind != "diagnose" or not outcome.success:
            return []
        binding_id = str(proposal.payload.get("prediction_failure_guard_id") or "")
        if binding_id:
            guard = self.guard(binding_id)
            if guard is None:
                return []
            explanation_complete = bool(
                outcome.effects.get("diagnostic_explanation_complete")
            )
            prediction_matched = (
                prediction is not None and prediction.status == "MATCHED"
            )
            if not explanation_complete or not prediction_matched:
                return []
            guard.state = PredictionGuardState.PROBATION
            guard.updated_cycle = cycle
            guard.diagnostic_outcome_id = outcome.outcome_id
            guard.diagnostic_explanation_refs = list(
                dict.fromkeys(
                    [
                        outcome.outcome_id,
                        *outcome.effects.get("explanation_evidence_refs", []),
                    ]
                )
            )
            for ref in guard.diagnostic_explanation_refs:
                if ref not in guard.evidence_refs:
                    guard.evidence_refs.append(ref)
            return [guard]
        dimension = str(proposal.payload.get("dimension") or "")
        if not dimension:
            return []
        target_path = f"internal_state.{dimension}"
        changed: list[PredictionFailureGuard] = []
        for guard in self.guards:
            if (
                guard.target_path == target_path
                and guard.state == PredictionGuardState.ACTIVE
            ):
                guard.state = PredictionGuardState.PROBATION
                guard.updated_cycle = cycle
                guard.diagnostic_outcome_id = outcome.outcome_id
                if outcome.outcome_id not in guard.evidence_refs:
                    guard.evidence_refs.append(outcome.outcome_id)
                changed.append(guard)
        return changed

    def remediation_proposals(self) -> list[ActionProposal]:
        proposals: list[ActionProposal] = []
        for guard in self.guards:
            if (
                guard.state != PredictionGuardState.ACTIVE
                or guard.diagnostic_outcome_id is not None
                or guard.diagnostic_attempts >= guard.max_diagnostic_attempts
            ):
                continue
            dimension = _dimension(guard.target_path)
            expected_range = _diagnostic_range(guard.anomaly_observed)
            if not dimension or expected_range is None:
                continue
            proposal = ActionProposal(
                kind="diagnose",
                resource="self",
                compute_cost=0.03,
                financial_cost=0.0,
                goal_id=None,
                reason=f"explain prediction failure guard {guard.guard_id}",
                payload={
                    "dimension": dimension,
                    "expected_range": expected_range,
                    "prediction_failure_guard_id": guard.guard_id,
                    "anomaly_expected": guard.anomaly_expected,
                    "anomaly_observed": guard.anomaly_observed,
                    "anomaly_evidence_refs": list(guard.evidence_refs),
                },
                trigger="INTERNAL",
                evidence=[guard.guard_id, *guard.evidence_refs],
                risk_domains=set(guard.risk_domains),
            )
            guard.diagnostic_attempts += 1
            proposals.append(proposal)
        return proposals

    def expire_stale(self, *, cycle: int) -> list[PredictionFailureGuard]:
        expired: list[PredictionFailureGuard] = []
        for guard in self.guards:
            if (
                guard.state in {
                    PredictionGuardState.ACTIVE,
                    PredictionGuardState.PROBATION,
                }
                and guard.expires_cycle is not None
                and cycle > guard.expires_cycle
            ):
                guard.state = PredictionGuardState.EXPIRED
                guard.updated_cycle = cycle
                expired.append(guard)
        return expired

    def guard(self, guard_id: str) -> PredictionFailureGuard | None:
        return next(
            (item for item in self.guards if item.guard_id == guard_id), None
        )

    def conflict(self, proposal: ActionProposal) -> PredictionFailureGuard | None:
        if proposal.trigger != "INTERNAL":
            return None
        target_path = proposal_target_path(proposal)
        proposal_domains = proposal_risk_domains(proposal)
        for guard in reversed(self.guards):
            if (
                proposal.kind == "diagnose"
                and str(
                    proposal.payload.get("prediction_failure_guard_id") or ""
                )
                == guard.guard_id
                and target_path == guard.target_path
            ):
                continue
            guard_domains = guard.risk_domains or risk_domains_for_target(
                guard.target_path
            )
            same_pair = (
                guard.action_kind == proposal.kind
                and guard.target_path == target_path
            )
            same_domain = bool(guard_domains & proposal_domains)
            if guard.state == PredictionGuardState.ACTIVE and (
                same_pair or same_domain
            ):
                return guard
            if (
                guard.state == PredictionGuardState.PROBATION
                and same_domain
                and not same_pair
            ):
                return guard
        return None

    def active(self) -> list[PredictionFailureGuard]:
        return [
            item
            for item in self.guards
            if item.state in {
                PredictionGuardState.ACTIVE,
                PredictionGuardState.PROBATION,
            }
        ]

    def _find(
        self,
        action_kind: str,
        target_path: str,
        states: set[PredictionGuardState],
    ) -> PredictionFailureGuard | None:
        return next(
            (
                item
                for item in reversed(self.guards)
                if item.action_kind == action_kind
                and item.target_path == target_path
                and item.state in states
            ),
            None,
        )

    @staticmethod
    def _add_evidence(
        guard: PredictionFailureGuard, prediction: SelfPrediction
    ) -> None:
        if prediction.prediction_id not in guard.failure_prediction_ids:
            guard.failure_prediction_ids.append(prediction.prediction_id)
        for ref in [prediction.prediction_id, *prediction.evidence_refs]:
            if ref and ref not in guard.evidence_refs:
                guard.evidence_refs.append(ref)


def proposal_target_path(proposal: ActionProposal) -> str:
    if proposal.kind in {"maintain", "diagnose"}:
        dimension = str(proposal.payload.get("dimension") or "")
        return f"internal_state.{dimension}" if dimension else ""
    if proposal.kind == "disable_capability":
        capability = str(proposal.payload.get("capability") or "llm_provider")
        return f"capabilities.{capability}"
    return ""


def proposal_risk_domains(proposal: ActionProposal) -> set[str]:
    domains = set(proposal.risk_domains)
    domains.update(risk_domains_for_target(proposal_target_path(proposal)))
    if proposal.kind in {"restore_config", "contain", "disable_capability"}:
        domains.add("runtime_integrity")
    if proposal.kind == "conserve":
        domains.add("resource_continuity")
    if proposal.kind in {"switch_provider", "probe_provider"}:
        domains.add("provider_continuity")
    return domains


def risk_domains_for_target(target_path: str) -> set[str]:
    lowered = target_path.lower()
    domains: set[str] = set()
    if any(token in lowered for token in ("memory", "integrity", "config", "signature")):
        domains.update({"runtime_integrity", "resource_continuity"})
    if any(token in lowered for token in ("compute", "storage", "resource")):
        domains.add("resource_continuity")
    if any(token in lowered for token in ("provider", "llm")):
        domains.add("provider_continuity")
    return domains


def _dimension(target_path: str) -> str:
    prefix = "internal_state."
    return target_path[len(prefix) :] if target_path.startswith(prefix) else ""


def _diagnostic_range(observed: Any) -> list[float] | None:
    if not isinstance(observed, (int, float)) or isinstance(observed, bool):
        return None
    value = float(observed)
    return [value, value]


def _prediction_risk_domains(prediction: SelfPrediction) -> set[str]:
    domains = risk_domains_for_target(prediction.target_path)
    action = str(prediction.intervention.get("action") or "")
    if action in {"restore_config", "contain", "disable_capability"}:
        domains.add("runtime_integrity")
    if action == "conserve":
        domains.add("resource_continuity")
    if action in {"switch_provider", "probe_provider"}:
        domains.add("provider_continuity")
    return domains
