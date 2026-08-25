from __future__ import annotations

from ..clock import new_id
from ..records import OutcomeRecord
from ..action.policy import ActionPolicy
from ..action.proposal import ActionProposal
from ..goals.goal import Goal
from ..governance.budget import ResourceBudget
from .command import ActuationCommand
from .registry import ActuatorRegistry


class ActuationGateway:
    """Single actuation exit: policy → command → actuator registry."""

    def __init__(self, policy_provider, actuators: ActuatorRegistry) -> None:
        self._policy_provider = policy_provider
        self.actuators = actuators
        self.last_route: str | None = None

    @property
    def policy(self) -> ActionPolicy:
        return self._policy_provider() if callable(self._policy_provider) else self._policy_provider

    def execute(
        self,
        proposal: ActionProposal,
        goals: list[Goal],
        budget: ResourceBudget,
        *,
        cycle: int,
        timestamp,
    ) -> tuple[OutcomeRecord | None, list[dict]]:
        authorization = self.policy.filter([proposal], goals, budget)
        if not authorization.authorized:
            self.last_route = "denied"
            return None, list(authorization.denied)
        command = ActuationCommand(
            kind=proposal.kind,
            payload=dict(proposal.payload),
            reason=proposal.reason,
            resource=proposal.resource,
        )
        result = self.actuators.execute(command)
        self.last_route = "actuator_registry"
        outcome = OutcomeRecord(
            outcome_id=new_id("out"),
            action_id=proposal.action_id,
            cycle=cycle,
            timestamp=timestamp,
            success=result.success,
            description=result.description,
            effects=dict(result.effects),
            evidence_result=result.evidence_result,
            missing_evidence_units=list(result.missing_evidence_units),
        )
        return outcome, []
