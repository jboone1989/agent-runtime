"""Orchestrate authorized specialist EXECUTION. Not a second authority."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
import math
from typing import Any

from ..action.policy import ActionPolicy
from ..action.proposal import ActionProposal
from ..backends.model import AgentEvent, AgentRequest, AgentResult, AgentRunMode
from ..backends.protocol import AgentBackend
from ..clock import Clock, new_id
from ..capabilities.inventory import CapabilityInventory
from ..capabilities.model import CapabilityLifecycleState
from ..context.projection import ContextPolicy, ContextProjector, ContextSnapshot
from ..goals.goal import Goal
from ..governance.budget import ResourceBudget
from ..planning.step import PlanStep
from ..records import ActionRecord, OutcomeRecord
from .evidence import bound_execution_evidence
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
        capability_inventory: CapabilityInventory | None = None,
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
        self.capability_inventory = capability_inventory
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
        max_duration_seconds: float | None = None,
        action_id: str | None = None,
    ) -> RuntimeExecutionResult:
        if max_duration_seconds is not None and (
            isinstance(max_duration_seconds, bool)
            or not isinstance(max_duration_seconds, (int, float))
            or not math.isfinite(max_duration_seconds)
            or max_duration_seconds <= 0
        ):
            return RuntimeExecutionResult(
                denied=({
                    "action": "workspace_execute",
                    "reason": "invalid execution duration",
                    "action_id": action_id,
                },)
            )
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

        unavailable = _unavailable_capabilities(
            required_capabilities, self.capability_inventory
        )
        if unavailable:
            return RuntimeExecutionResult(
                denied=(
                    {
                        "action": proposal.kind,
                        "reason": "capability binding is not executable",
                        "capabilities": unavailable,
                        "action_id": proposal.action_id,
                    },
                )
            )

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

        if max_duration_seconds is not None and not backend.capabilities.cancellable_execution:
            return RuntimeExecutionResult(
                denied=(
                    {
                        "action": proposal.kind,
                        "reason": "backend does not support bounded cancellation",
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
            max_duration_seconds=max_duration_seconds,
        )
        try:
            backend_result = await _run_bounded(backend, request, max_duration_seconds)
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
        except Exception as exc:
            # A provider failure is still a completed Runtime observation.
            # Convert it to the provider-neutral result contract so callers
            # receive an auditable OutcomeRecord instead of an uncaught error.
            backend_result = AgentResult(
                final_response="",
                status="FAILED",
                error=f"{type(exc).__name__}: {exc}",
            )

        execution_completed = backend_result.status == "COMPLETED"
        verification = self.verifier.verify(
            expected=verification_spec,
            backend_result=backend_result,
            workspace=self.workspace,
        )
        side_effect_violations = _side_effect_violations(
            backend_result, execution_policy
        )
        if side_effect_violations:
            verification = verification.__class__(
                status="FAIL",
                summary="provider reported a side-effect policy violation",
                verified_success=False,
                evidence_result="COVERED",
                details={
                    **verification.details,
                    "side_effect_violations": side_effect_violations,
                },
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
            success=execution_completed and verification.verified_success,
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
            verified_success=execution_completed and verification.verified_success,
            used_capability_id=used,
            effects=effects,
        )

async def _run_bounded(
    backend: AgentBackend,
    request: AgentRequest,
    max_duration_seconds: float | None,
) -> AgentResult:
    """Run a backend under its declared cooperative cancellation contract."""
    task = asyncio.create_task(backend.run(request))
    try:
        if max_duration_seconds is None:
            return await task
        _done, pending = await asyncio.wait({task}, timeout=max_duration_seconds)
        if not pending:
            return task.result()
    except asyncio.CancelledError:
        task.cancel()
        try:
            await task
        except BaseException:
            pass
        raise

    task.cancel()
    cleanup_error: str | None = None
    try:
        result = await task
        if isinstance(result, AgentResult):
            # A backend that catches cancellation may return proof (or a
            # bounded-cancellation failure) after its native cleanup protocol.
            return result
    except asyncio.CancelledError:
        pass
    except BaseException as exc:
        cleanup_error = type(exc).__name__
    payload: dict[str, Any] = {"max_duration_seconds": max_duration_seconds}
    if cleanup_error:
        payload["cancellation_cleanup_error"] = cleanup_error
    return AgentResult(
        final_response="",
        status="TIMED_OUT",
        events=(
            AgentEvent(
                kind="runtime.execution_timed_out",
                summary="Runtime cancelled the backend at its execution deadline",
                payload=payload,
            ),
        ),
        error="execution exceeded its Runtime time bound",
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
    # Persistence boundary: backend-supplied payloads pass through the evidence
    # gate so secrets never reach durable records regardless of the adapter.
    return bound_execution_evidence(
        {
            "backend_status": backend_result.status,
            "backend_response": backend_result.final_response,
            "backend_error": backend_result.error,
            "backend_usage": (
                None
                if backend_result.usage is None
                else {
                    "input_tokens": backend_result.usage.input_tokens,
                    "output_tokens": backend_result.usage.output_tokens,
                }
            ),
            "backend_events": backend_result.events,
            "verification_status": verification.status,
            "verification_summary": verification.summary,
            "execution_completed": execution_completed,
            "verified_success": execution_completed and verification.verified_success,
            "artifacts": [item.uri for item in backend_result.artifacts],
            "session_ref": backend_result.session_ref,
            "required_capabilities": sorted(required_capabilities),
            "verification_details": dict(verification.details),
        }
    )


def _unavailable_capabilities(
    required: set[str], inventory: CapabilityInventory | None
) -> list[str]:
    if inventory is None:
        return []
    unavailable: list[str] = []
    for capability_id in required:
        binding = inventory.get(capability_id)
        if (
            binding is None
            or binding.state is not CapabilityLifecycleState.ACTIVE
            or not inventory.usable(capability_id)
        ):
            unavailable.append(capability_id)
    return sorted(unavailable)


def _side_effect_violations(
    backend_result: AgentResult, execution_policy
) -> list[dict[str, Any]]:
    """Extract explicit provider-reported violations for Runtime adjudication.

    Ordinary backend events remain evidence, not authority. Only the
    deterministic violation shape is interpreted here, which keeps arbitrary
    provider payloads out of the policy decision.
    """
    violations: list[dict[str, Any]] = []
    for event in backend_result.events:
        payload = dict(event.payload)
        raw = payload.get("side_effect_violations")
        if raw is None and event.kind == "backend.side_effect_violation":
            raw = [payload]
        if not isinstance(raw, list):
            continue
        for item in raw:
            if isinstance(item, dict):
                violation = {str(key): value for key, value in item.items()}
            else:
                violation = {"description": str(item)}
            violation.setdefault("allowed_tools", sorted(execution_policy.allowed_tools))
            violation.setdefault("allowed_resources", sorted(execution_policy.allowed_resources))
            violations.append(violation)
    return violations
