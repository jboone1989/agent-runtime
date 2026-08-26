"""Orchestrate authorized specialist EXECUTION. Not a second authority."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from ..action.policy import ActionPolicy
from ..action.proposal import ActionProposal
from ..backends.model import AgentRequest, AgentResult, AgentRunMode
from ..backends.protocol import AgentBackend
from ..clock import Clock, new_id
from ..context.projection import ContextPolicy, ContextProjector, ContextSnapshot
from ..goals.goal import Goal
from ..governance.budget import ResourceBudget
from ..planning.step import PlanStep
from ..records import ActionRecord, OutcomeRecord
from .policy import (
    map_execution_policy,
    requested_networks_from_step,
    requested_tools_from_step,
    target_resources_from_step,
    workspace_write_requested,
)
from .records import RuntimeExecutionResult
from .request import VerificationSpec
from .verification import RuntimeVerifier, WorkspaceView

EXECUTION_CONTEXT_SECTIONS = frozenset(
    {
        "goal",
        "current_plan",
        "plan_step",
        "workspace",
        "relevant_evidence",
    }
)


class RuntimeExecutionGateway:
    """Policy → backend EXECUTION → independent verification → records."""

    def __init__(
        self,
        *,
        policy_provider: Callable[[], ActionPolicy] | ActionPolicy,
        backend_provider: Callable[[], AgentBackend | None] | AgentBackend | None,
        verifier: RuntimeVerifier,
        workspace: WorkspaceView,
        clock: Clock,
        cycle_provider: Callable[[], int],
        goals_provider: Callable[[], list[Goal]],
        budget_provider: Callable[[], ResourceBudget],
        projector: ContextProjector | None = None,
    ) -> None:
        self._policy_provider = policy_provider
        self._backend_provider = backend_provider
        self.verifier = verifier
        self.workspace = workspace
        self.clock = clock
        self._cycle_provider = cycle_provider
        self._goals_provider = goals_provider
        self._budget_provider = budget_provider
        self.projector = projector or ContextProjector()

    @property
    def policy(self) -> ActionPolicy:
        provider = self._policy_provider
        return provider() if callable(provider) else provider

    @property
    def backend(self) -> AgentBackend | None:
        provider = self._backend_provider
        return provider() if callable(provider) else provider

    async def execute(
        self,
        *,
        plan_step: PlanStep,
        task: str,
        required_capabilities: set[str],
        target_resources: set[str],
        verification_spec: VerificationSpec,
        goal_id: str | None = None,
        context_snapshot: ContextSnapshot | None = None,
        trigger: str = "USER",
        goal_origin: str | None = None,
        reason: str | None = None,
        evidence: list[str] | None = None,
        compute_cost: float = 0.02,
        financial_cost: float = 0.0,
        action_id: str | None = None,
    ) -> RuntimeExecutionResult:
        resources = set(target_resources) or target_resources_from_step(plan_step)
        resource = "workspace" if "workspace" in resources else next(iter(sorted(resources)), "workspace")
        proposal = ActionProposal(
            kind="workspace_execute",
            resource=resource,
            compute_cost=compute_cost,
            financial_cost=financial_cost,
            goal_id=goal_id,
            reason=reason or task or plan_step.objective,
            payload=dict(plan_step.payload),
            trigger=trigger,
            goal_origin=goal_origin,
            evidence=list(evidence or plan_step.evidence_refs),
            action_id=action_id or new_id("act"),
        )
        authorization = self.policy.filter(
            [proposal], self._goals_provider(), self._budget_provider()
        )
        if not authorization.authorized:
            return RuntimeExecutionResult(denied=tuple(authorization.denied))

        backend = self.backend
        if backend is None or not backend.capabilities.workspace_execution:
            return RuntimeExecutionResult(
                denied=(
                    {
                        "action": proposal.kind,
                        "reason": "no execution backend",
                        "action_id": proposal.action_id,
                    },
                )
            )

        execution_policy = map_execution_policy(
            autonomy=self.policy.autonomy,
            target_resources=resources,
            requested_tools=requested_tools_from_step(plan_step),
            requested_networks=requested_networks_from_step(plan_step),
            workspace_write=workspace_write_requested(plan_step),
        )
        bounded = self.projector.project(
            context_snapshot or ContextSnapshot(sections={}),
            mode=AgentRunMode.EXECUTION,
            policy=ContextPolicy(allowed_sections=EXECUTION_CONTEXT_SECTIONS),
        )
        request = AgentRequest(
            mode=AgentRunMode.EXECUTION,
            task=task,
            context=bounded.to_dict(),
            policy=execution_policy,
            token_budget=max(0, int(self._budget_provider().remaining_compute * 1000)),
            compute_budget=max(0.0, float(self._budget_provider().remaining_compute)),
        )
        try:
            backend_result = await backend.run(request)
        except PermissionError as exc:
            return RuntimeExecutionResult(
                denied=(
                    {
                        "action": proposal.kind,
                        "reason": str(exc),
                        "action_id": proposal.action_id,
                    },
                )
            )

        execution_completed = backend_result.status == "COMPLETED"
        verification = self.verifier.verify(
            expected=verification_spec,
            backend_result=backend_result,
            workspace=self.workspace,
        )
        timestamp = self.clock.now()
        cycle = int(self._cycle_provider())
        action = _action_record(proposal, cycle, timestamp)
        effects = _execution_effects(
            backend_result,
            verification,
            execution_completed=execution_completed,
            required_capabilities=required_capabilities,
        )
        outcome = OutcomeRecord(
            outcome_id=new_id("out"),
            action_id=proposal.action_id,
            cycle=cycle,
            timestamp=timestamp,
            success=verification.verified_success,
            description=(
                "workspace execution verified"
                if verification.verified_success
                else f"workspace execution verification {verification.status.lower()}"
            ),
            effects=effects,
            evidence_result=verification.evidence_result,
        )
        used = (
            "workspace_execution"
            if "workspace_execution" in required_capabilities
            else proposal.kind
        )
        return RuntimeExecutionResult(
            action=action,
            outcome=outcome,
            backend_result=backend_result,
            verification=verification,
            execution_completed=execution_completed,
            verified_success=verification.verified_success,
            used_capability_id=used,
            effects=effects,
        )


def run_sync(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError(
        "RuntimeExecutionGateway.execute must be awaited inside a running event loop"
    )


def _action_record(proposal: ActionProposal, cycle: int, timestamp) -> ActionRecord:
    return ActionRecord(
        action_id=proposal.action_id,
        cycle=cycle,
        timestamp=timestamp,
        kind=proposal.kind,
        resource=proposal.resource,
        goal_id=proposal.goal_id,
        payload=dict(proposal.payload),
        compute_cost=proposal.compute_cost,
        financial_cost=proposal.financial_cost,
        trigger=proposal.trigger,
        goal_origin=proposal.goal_origin,
        reason=proposal.reason,
        evidence=list(proposal.evidence),
    )


def _execution_effects(
    backend_result: AgentResult,
    verification,
    *,
    execution_completed: bool,
    required_capabilities: set[str],
) -> dict[str, Any]:
    return {
        "backend_status": backend_result.status,
        "backend_response": backend_result.final_response,
        "verification_status": verification.status,
        "verification_summary": verification.summary,
        "execution_completed": execution_completed,
        "verified_success": verification.verified_success,
        "artifacts": [item.uri for item in backend_result.artifacts],
        "session_ref": backend_result.session_ref,
        "required_capabilities": sorted(required_capabilities),
        "verification_details": dict(verification.details),
    }
