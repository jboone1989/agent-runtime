"""One-shot Agent-selected experiments executed through the normal action gateway."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..action.proposal import ActionProposal
from ..clock import new_id
from ..records import OutcomeRecord
from ..self_model.beliefs import SelfPrediction
from ..self_model.failure_adaptation import proposal_target_path


class AgencyExperimentStatus(str, Enum):
    PLANNED = "PLANNED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    DENIED = "DENIED"


class ExperimentFailureKind(str, Enum):
    DENIED = "DENIED"
    ACTION_FAILED = "ACTION_FAILED"
    PREDICTION_VIOLATED = "PREDICTION_VIOLATED"
    MISSING_PREDICTION = "MISSING_PREDICTION"


class ExperimentFailureBindingStatus(str, Enum):
    ACTIVE = "ACTIVE"
    RESOLVED = "RESOLVED"
    EXPIRED = "EXPIRED"


@dataclass
class ExperimentFailureBinding:
    binding_id: str
    experiment_id: str
    thread_id: str
    action_kind: str
    target_path: str
    failure_kind: ExperimentFailureKind
    status: ExperimentFailureBindingStatus
    created_cycle: int
    expires_cycle: int
    prediction_id: str | None = None
    outcome_id: str | None = None
    failure_evidence_refs: list[str] = field(default_factory=list)
    structured_denial: dict[str, Any] = field(default_factory=dict)
    linked_guard_id: str | None = None
    linked_selection_concern_id: str | None = None
    diagnostic_outcome_id: str | None = None
    recovery_prediction_id: str | None = None


@dataclass
class AgencyExperiment:
    experiment_id: str
    thread_id: str
    statement: str
    action_kind: str
    resource: str
    payload: dict[str, Any]
    scheduled_cycle: int
    status: AgencyExperimentStatus
    evidence_refs: list[str] = field(default_factory=list)
    requires_prediction: bool = True
    action_id: str | None = None
    outcome_id: str | None = None
    prediction_id: str | None = None
    denial_reason: str | None = None
    result_summary: str | None = None
    failure_binding_id: str | None = None
    denial_details: dict[str, Any] = field(default_factory=dict)


class AgencyExperimentManager:
    def __init__(
        self,
        experiments: list[AgencyExperiment] | None = None,
        failure_bindings: list[ExperimentFailureBinding] | None = None,
        *,
        expiry_cycles: int = 24,
    ) -> None:
        self.experiments = list(experiments or [])
        self.failure_bindings = list(failure_bindings or [])
        self.expiry_cycles = expiry_cycles

    def schedule(self, experiment: AgencyExperiment) -> AgencyExperiment:
        existing = self.experiment(experiment.experiment_id)
        if existing is not None:
            return existing
        if experiment.status != AgencyExperimentStatus.PLANNED:
            raise ValueError("new agency experiment must be PLANNED")
        if not experiment.evidence_refs:
            raise ValueError("agency experiment requires selection evidence")
        self.experiments.append(experiment)
        return experiment

    def due_proposals(self, cycle: int) -> list[ActionProposal]:
        self.expire(cycle)
        proposals: list[ActionProposal] = []
        for item in self.experiments:
            if item.status != AgencyExperimentStatus.PLANNED:
                continue
            if cycle < item.scheduled_cycle:
                continue
            proposal = ActionProposal(
                kind=item.action_kind,
                resource=item.resource,
                compute_cost=0.01,
                financial_cost=0.0,
                goal_id=None,
                reason=f"Agent-selected experiment {item.experiment_id}",
                payload={**item.payload, "agency_experiment_id": item.experiment_id},
                trigger="INTERNAL",
                evidence=[item.experiment_id, item.thread_id, *item.evidence_refs],
            )
            item.status = AgencyExperimentStatus.RUNNING
            item.action_id = proposal.action_id
            proposals.append(proposal)
        return proposals

    def observe_denial(
        self, proposal: ActionProposal, denial: dict, *, cycle: int
    ) -> AgencyExperiment | None:
        item = self._for_proposal(proposal)
        if item is None:
            return None
        item.status = AgencyExperimentStatus.DENIED
        item.denial_reason = str(denial.get("reason") or "denied")
        item.denial_details = _structured_denial(denial)
        item.result_summary = "experiment proposal was denied by action policy"
        binding = self._bind_failure(
            item,
            proposal,
            kind=ExperimentFailureKind.DENIED,
            cycle=cycle,
            structured_denial=item.denial_details,
            evidence_refs=_denial_evidence(item, proposal, item.denial_details),
        )
        item.failure_binding_id = binding.binding_id
        return item

    def observe_outcome(
        self,
        proposal: ActionProposal,
        outcome: OutcomeRecord,
        prediction: SelfPrediction | None,
        *,
        cycle: int,
        linked_guard_id: str | None = None,
    ) -> AgencyExperiment | None:
        item = self._for_proposal(proposal)
        if item is None:
            return None
        item.outcome_id = outcome.outcome_id
        item.prediction_id = None if prediction is None else prediction.prediction_id
        prediction_ok = (
            prediction is not None and prediction.status == "MATCHED"
            if item.requires_prediction
            else prediction is None or prediction.status == "MATCHED"
        )
        item.status = (
            AgencyExperimentStatus.COMPLETED
            if outcome.success and prediction_ok
            else AgencyExperimentStatus.FAILED
        )
        item.result_summary = (
            f"outcome_success={outcome.success}; "
            f"prediction_status={None if prediction is None else prediction.status}; "
            f"prediction_error={None if prediction is None else prediction.error}"
        )
        failure_kind = _failure_kind(item, outcome, prediction)
        if failure_kind is not None:
            binding = self._bind_failure(
                item,
                proposal,
                kind=failure_kind,
                cycle=cycle,
                prediction=prediction,
                outcome=outcome,
                linked_guard_id=linked_guard_id,
                evidence_refs=_failure_evidence(
                    item, proposal, outcome, prediction
                ),
            )
            item.failure_binding_id = binding.binding_id
        return item

    def observe_recovery(
        self,
        proposal: ActionProposal,
        outcome: OutcomeRecord,
        prediction: SelfPrediction | None,
        *,
        cycle: int,
    ) -> list[ExperimentFailureBinding]:
        self.expire(cycle)
        target_path = proposal_target_path(proposal)
        if not target_path or proposal.trigger != "INTERNAL":
            return []
        resolved: list[ExperimentFailureBinding] = []
        for binding in self.failure_bindings:
            if (
                binding.status != ExperimentFailureBindingStatus.ACTIVE
                or binding.target_path != target_path
                or binding.outcome_id == outcome.outcome_id
            ):
                continue
            if proposal.kind == "diagnose" and outcome.success and _structured(outcome):
                binding.status = ExperimentFailureBindingStatus.RESOLVED
                binding.diagnostic_outcome_id = outcome.outcome_id
                resolved.append(binding)
            elif (
                proposal.kind == binding.action_kind
                and outcome.success
                and prediction is not None
                and prediction.status == "MATCHED"
                and prediction.prediction_id != binding.prediction_id
            ):
                binding.status = ExperimentFailureBindingStatus.RESOLVED
                binding.recovery_prediction_id = prediction.prediction_id
                resolved.append(binding)
        return resolved

    def link_selection_concerns(self, concerns: list[Any]) -> None:
        for concern in concerns:
            for binding in reversed(self.failure_bindings):
                if (
                    binding.status == ExperimentFailureBindingStatus.ACTIVE
                    and binding.outcome_id in concern.evidence_refs
                ):
                    binding.linked_selection_concern_id = concern.concern_id
                    if concern.concern_id not in binding.failure_evidence_refs:
                        binding.failure_evidence_refs.append(concern.concern_id)
                    break

    def expire(self, cycle: int) -> list[ExperimentFailureBinding]:
        expired: list[ExperimentFailureBinding] = []
        for binding in self.failure_bindings:
            if (
                binding.status == ExperimentFailureBindingStatus.ACTIVE
                and cycle > binding.expires_cycle
            ):
                binding.status = ExperimentFailureBindingStatus.EXPIRED
                expired.append(binding)
        return expired

    def _bind_failure(
        self,
        item: AgencyExperiment,
        proposal: ActionProposal,
        *,
        kind: ExperimentFailureKind,
        cycle: int,
        evidence_refs: list[str],
        prediction: SelfPrediction | None = None,
        outcome: OutcomeRecord | None = None,
        structured_denial: dict[str, Any] | None = None,
        linked_guard_id: str | None = None,
    ) -> ExperimentFailureBinding:
        target_path = proposal_target_path(proposal)
        existing = next(
            (
                binding
                for binding in reversed(self.failure_bindings)
                if binding.action_kind == proposal.kind
                and binding.target_path == target_path
                and binding.status == ExperimentFailureBindingStatus.ACTIVE
                and cycle - binding.created_cycle <= self.expiry_cycles
            ),
            None,
        )
        if existing is not None:
            for ref in evidence_refs:
                if ref and ref not in existing.failure_evidence_refs:
                    existing.failure_evidence_refs.append(ref)
            if linked_guard_id:
                existing.linked_guard_id = linked_guard_id
            return existing
        binding = ExperimentFailureBinding(
            binding_id=new_id("apxfb"),
            experiment_id=item.experiment_id,
            thread_id=item.thread_id,
            action_kind=proposal.kind,
            target_path=target_path,
            failure_kind=kind,
            status=ExperimentFailureBindingStatus.ACTIVE,
            created_cycle=cycle,
            expires_cycle=cycle + self.expiry_cycles,
            prediction_id=None if prediction is None else prediction.prediction_id,
            outcome_id=None if outcome is None else outcome.outcome_id,
            failure_evidence_refs=list(dict.fromkeys(ref for ref in evidence_refs if ref)),
            structured_denial=dict(structured_denial or {}),
            linked_guard_id=linked_guard_id,
        )
        self.failure_bindings.append(binding)
        return binding

    def experiment(self, experiment_id: str) -> AgencyExperiment | None:
        return next(
            (item for item in self.experiments if item.experiment_id == experiment_id),
            None,
        )

    def _for_proposal(self, proposal: ActionProposal) -> AgencyExperiment | None:
        experiment_id = str(proposal.payload.get("agency_experiment_id") or "")
        item = self.experiment(experiment_id)
        if item is None or item.action_id != proposal.action_id:
            return None
        return item


def _failure_kind(
    item: AgencyExperiment,
    outcome: OutcomeRecord,
    prediction: SelfPrediction | None,
) -> ExperimentFailureKind | None:
    if not outcome.success:
        return ExperimentFailureKind.ACTION_FAILED
    if item.requires_prediction and prediction is None:
        return ExperimentFailureKind.MISSING_PREDICTION
    if prediction is not None and prediction.status == "VIOLATED":
        return ExperimentFailureKind.PREDICTION_VIOLATED
    if item.requires_prediction and prediction is not None and prediction.status != "MATCHED":
        return ExperimentFailureKind.MISSING_PREDICTION
    return None


def _failure_evidence(
    item: AgencyExperiment,
    proposal: ActionProposal,
    outcome: OutcomeRecord,
    prediction: SelfPrediction | None,
) -> list[str]:
    return list(
        dict.fromkeys(
            ref
            for ref in [
                item.experiment_id,
                item.thread_id,
                proposal.action_id,
                outcome.outcome_id,
                None if prediction is None else prediction.prediction_id,
                *item.evidence_refs,
                *([] if prediction is None else prediction.evidence_refs),
                *list(outcome.effects.get("evidence_refs") or []),
            ]
            if ref
        )
    )


def _structured_denial(denial: dict[str, Any]) -> dict[str, Any]:
    for key, category, id_key in (
        ("commitment_conflict", "COMMITMENT", "commitment_id"),
        ("prediction_failure_guard", "PREDICTION_GUARD", "guard_id"),
        ("agency_discovery", "AGENCY_DISCOVERY", "discovery_id"),
    ):
        details = denial.get(key)
        if isinstance(details, dict):
            return {
                "category": category,
                "authority_ref": details.get(id_key),
                "reason": denial.get("reason"),
                "evidence_refs": list(details.get("evidence_refs") or []),
            }
    return {
        "category": "AUTHORIZATION",
        "authority_ref": "autonomy.allowed_actions",
        "reason": denial.get("reason"),
        "evidence_refs": ["policy:autonomy.allowed_actions"],
    }


def _denial_evidence(
    item: AgencyExperiment,
    proposal: ActionProposal,
    structured: dict[str, Any],
) -> list[str]:
    return list(
        dict.fromkeys(
            ref
            for ref in [
                item.experiment_id,
                item.thread_id,
                proposal.action_id,
                structured.get("authority_ref"),
                *item.evidence_refs,
                *structured.get("evidence_refs", []),
            ]
            if ref
        )
    )


def _structured(outcome: OutcomeRecord) -> bool:
    effects = outcome.effects
    return (
        effects.get("value") is not None
        and effects.get("uncertainty") is not None
        and bool(effects.get("evidence_refs"))
    )
