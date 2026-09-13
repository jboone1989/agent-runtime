"""Map Runtime authorization onto a backend ExecutionPolicy.

Backend permissions are always a subset of Runtime permissions. Anything
that cannot be mapped with certainty is denied by default.
"""
from __future__ import annotations

from ..backends.model import ExecutionPolicy
from ..capabilities.model import SideEffectLevel
from ..governance.autonomy import AutonomyPolicy
from ..planning.step import PlanStep


def map_execution_policy(
    *,
    autonomy: AutonomyPolicy,
    target_resources: set[str],
    requested_tools: set[str],
    requested_networks: set[str] | None = None,
    workspace_write: bool = False,
) -> ExecutionPolicy:
    runtime_resources = set(autonomy.allowed_resources)
    runtime_tools = set(autonomy.allowed_actions)
    runtime_networks = set(autonomy.allowed_networks)
    approval = set(autonomy.require_human_approval)

    allowed_resources = frozenset(
        resource for resource in target_resources if resource in runtime_resources
    )
    requested = set(requested_tools)
    require_approval = frozenset(tool for tool in requested if tool in approval)
    allowed_tools = frozenset(
        tool
        for tool in requested
        if tool in runtime_tools and tool not in require_approval
    )
    allowed_networks = frozenset(
        network
        for network in (requested_networks or set())
        if network in runtime_networks
    )
    write = bool(
        workspace_write
        and "workspace" in allowed_resources
        and "workspace" in runtime_resources
    )
    return ExecutionPolicy(
        allowed_tools=allowed_tools,
        allowed_resources=allowed_resources,
        allowed_networks=allowed_networks,
        workspace_write=write,
        require_approval=require_approval,
    )


def workspace_write_requested(plan_step: PlanStep) -> bool:
    if plan_step.payload.get("workspace_write"):
        return True
    for requirement in plan_step.required_capabilities:
        if "workspace_write" in requirement.required_permissions:
            return True
        if requirement.max_side_effect.value >= SideEffectLevel.LOCAL_STATE.value:
            return True
    return False


def requested_tools_from_step(plan_step: PlanStep) -> set[str]:
    raw = plan_step.payload.get("allowed_tools")
    if raw:
        return {str(item) for item in raw}
    tools = {"workspace_execute"}
    for requirement in plan_step.required_capabilities:
        tools.update(str(item) for item in requirement.required_permissions)
    return tools


def requested_networks_from_step(plan_step: PlanStep) -> set[str]:
    raw = plan_step.payload.get("allowed_networks") or ()
    return {str(item) for item in raw}


def target_resources_from_step(plan_step: PlanStep) -> set[str]:
    raw = plan_step.payload.get("target_resources")
    if raw:
        return {str(item) for item in raw}
    resources = {"workspace"}
    for requirement in plan_step.required_capabilities:
        extra = requirement.constraints.get("resources") or ()
        resources.update(str(item) for item in extra)
    return resources
