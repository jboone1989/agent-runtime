"""Wrap existing ActionExecutor handlers. Do not rewrite business logic."""
from __future__ import annotations

from ..action.executor import ActionExecutor
from ..action.proposal import ActionProposal
from ..self_model.model import SelfModel
from .command import ActuationCommand
from .result import ActuationResult


class ExecutorBackedActuator:
    def __init__(
        self,
        *,
        actuator_id: str,
        kinds: set[str],
        executor: ActionExecutor,
        self_model: SelfModel,
        cycle_fn,
        now_fn,
    ) -> None:
        self.actuator_id = actuator_id
        self._kinds = kinds
        self._executor = executor
        self._self_model = self_model
        self._cycle_fn = cycle_fn
        self._now_fn = now_fn

    def capabilities(self) -> set[str]:
        return set(self._kinds)

    def bind(self, self_model: SelfModel) -> None:
        self._self_model = self_model

    def execute(self, command: ActuationCommand) -> ActuationResult:
        proposal = ActionProposal(
            kind=command.kind,
            resource=command.resource,
            compute_cost=0.02,
            financial_cost=0.0,
            goal_id=None,
            reason=command.reason,
            payload=dict(command.payload),
            trigger="REGULATION",
        )
        outcome = self._executor.execute(
            proposal,
            self._self_model,
            cycle=int(self._cycle_fn()),
            timestamp=self._now_fn(),
        )
        return ActuationResult(
            command_id=command.command_id,
            success=outcome.success,
            description=outcome.description,
            effects=dict(outcome.effects),
            evidence_result=outcome.evidence_result,
            missing_evidence_units=list(outcome.missing_evidence_units),
        )


def digital_actuators(
    executor: ActionExecutor,
    self_model: SelfModel,
    cycle_fn,
    now_fn,
) -> list[ExecutorBackedActuator]:
    common = dict(executor=executor, self_model=self_model, cycle_fn=cycle_fn, now_fn=now_fn)
    return [
        ExecutorBackedActuator(
            actuator_id="provider_actuator",
            kinds={"switch_provider", "probe_provider"},
            **common,
        ),
        ExecutorBackedActuator(
            actuator_id="memory_actuator",
            kinds={"record"},
            **common,
        ),
        ExecutorBackedActuator(
            actuator_id="config_actuator",
            kinds={"restore_config"},
            **common,
        ),
        ExecutorBackedActuator(
            actuator_id="runtime_actuator",
            kinds={"noop", "introspect", "diagnose", "maintain", "conserve", "contain", "disable_capability"},
            **common,
        ),
    ]
