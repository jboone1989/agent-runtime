"""Wrap SimplePlanner so v0.2.2 tests keep calling the same algorithm."""
from __future__ import annotations

from ..action.proposal import ActionProposal
from ..capabilities.model import CapabilityRequirement
from ..cognition.planner import SimplePlanner
from ..executive.records import ExecutiveDecision
from ..experience.refs import stable_ref
from ..goals.goal import Goal, GoalOrigin, GoalStatus
from ..motivation.engine import MotivationState
from ..time.waiting import WaitingState
from .plan import Plan
from .state import PlanStatus
from .step import PlanStep


class LegacySimplePlannerAdapter:
    def __init__(self, planner: SimplePlanner | None = None) -> None:
        self._planner = planner or SimplePlanner()

    def plan(self, goals: list[Goal], motivation: MotivationState) -> list[ActionProposal]:
        return self._planner.plan(goals, motivation)

    def ensure_user_plan(self, goals: list[Goal], existing: list[Plan], cycle: int) -> list[Plan]:
        user = [
            goal
            for goal in goals
            if goal.origin == GoalOrigin.USER and goal.status == GoalStatus.ACTIVE
        ]
        plans = list(existing)
        for goal in user:
            covering = [
                plan
                for plan in plans
                if goal.goal_id in plan.goal_ids
                and plan.status not in {PlanStatus.FAILED, PlanStatus.CANCELLED}
            ]
            if goal.auto_complete and covering:
                continue
            if (
                not goal.auto_complete
                and any(plan.status != PlanStatus.COMPLETED for plan in covering)
            ):
                continue
            renewal = (
                sum(plan.status == PlanStatus.COMPLETED for plan in covering)
                if not goal.auto_complete
                else 0
            )
            plans.append(_two_step_plan(goal, cycle, renewal=renewal))
        return plans

    def apply_decision(
        self,
        plan: Plan | None,
        decision: ExecutiveDecision,
        cycle: int,
    ) -> tuple[Plan | None, WaitingState | None]:
        if plan is None:
            return None, None
        if decision.interrupt_plan:
            plan.status = PlanStatus.WAITING
            wait = WaitingState(
                wait_id=stable_ref("wait", plan.plan_id, cycle, "interrupt"),
                plan_id=plan.plan_id,
                reason="unresolved high-severity threat",
                until="threat_resolved",
                created_cycle=cycle,
                evidence_refs=list(decision.evidence_refs),
            )
            return plan, wait
        if decision.request_replan:
            plan.status = PlanStatus.BLOCKED
            if plan.current_step is not None:
                plan.current_step.status = PlanStatus.BLOCKED
            return plan, None
        if decision.continue_plan and plan.status == PlanStatus.WAITING:
            plan.status = PlanStatus.ACTIVE
        return plan, None

    def advance(self, plan: Plan | None, executed_kinds: list[str]) -> None:
        if plan is None or plan.status != PlanStatus.ACTIVE:
            return
        step = plan.current_step
        if step is None or step.objective not in executed_kinds:
            return
        step.status = PlanStatus.COMPLETED
        index = plan.steps.index(step)
        if index + 1 < len(plan.steps):
            nxt = plan.steps[index + 1]
            nxt.status = PlanStatus.ACTIVE
            plan.current_step_id = nxt.step_id
            return
        plan.status = PlanStatus.COMPLETED


def filter_proposals(
    proposals: list[ActionProposal],
    plan: Plan | None,
    decision: ExecutiveDecision | None,
) -> list[ActionProposal]:
    """Keep SimplePlanner output, but only one current plan step per tick.

    Survival / failover kinds that are not plan-step objectives still pass through
    so v0.2.2 tests (switch_provider, conserve) stay green.
    """
    if plan is None:
        return proposals
    step_kinds = {step.objective for step in plan.steps}
    paused = (
        decision is not None
        and (decision.interrupt_plan or decision.request_replan)
    ) or plan.status in {PlanStatus.WAITING, PlanStatus.BLOCKED}
    if paused:
        return [proposal for proposal in proposals if proposal.kind not in step_kinds]
    step = plan.current_step
    current = None if step is None else step.objective
    emitted_current = False
    out: list[ActionProposal] = []
    for proposal in proposals:
        if proposal.kind == current and not emitted_current:
            out.append(proposal)
            emitted_current = True
        elif proposal.kind not in step_kinds:
            out.append(proposal)
    if (
        current is not None
        and not emitted_current
        and not paused
        and plan.status == PlanStatus.ACTIVE
    ):
        template = next(
            (proposal for proposal in proposals if proposal.goal_id in plan.goal_ids),
            None,
        )
        payload = {} if step is None else dict(step.payload)
        if current == "maintain" and not payload:
            payload = {"dimension": "operational_integrity"}
        out.insert(
            0,
            ActionProposal(
                kind=current,
                resource=_resource_for_step(current),
                compute_cost=0.02,
                financial_cost=0.0,
                goal_id=(
                    template.goal_id
                    if template is not None
                    else (plan.goal_ids[0] if plan.goal_ids else None)
                ),
                reason=f"execute plan step: {current}",
                payload=payload,
                trigger="USER" if template is None else template.trigger,
                goal_origin="USER" if template is None else template.goal_origin,
                evidence=[] if step is None else list(step.evidence_refs),
            ),
        )
    return out


def _two_step_plan(goal: Goal, cycle: int, *, renewal: int = 0) -> Plan:
    identity = (
        (goal.goal_id,)
        if renewal <= 0
        else (goal.goal_id, f"renewal:{renewal}")
    )
    first = PlanStep(
        step_id=stable_ref("stp", *identity, "introspect"),
        objective="introspect",
        required_capabilities=[CapabilityRequirement(capability_id="introspect")],
        status=PlanStatus.ACTIVE,
        evidence_refs=list(goal.evidence_refs),
        caused_by=[goal.goal_id],
    )
    second = PlanStep(
        step_id=stable_ref("stp", *identity, "maintain"),
        objective="maintain",
        required_capabilities=[CapabilityRequirement(capability_id="maintain")],
        payload={"dimension": "operational_integrity"},
        status=PlanStatus.PROPOSED,
        evidence_refs=list(goal.evidence_refs),
        caused_by=[goal.goal_id],
    )
    return Plan(
        plan_id=stable_ref("plan", *identity),
        goal_ids=[goal.goal_id],
        steps=[first, second],
        current_step_id=first.step_id,
        status=PlanStatus.ACTIVE,
        created_cycle=cycle,
        caused_by=[goal.goal_id],
    )


def _resource_for_step(kind: str) -> str:
    if kind == "record":
        return "memory"
    if kind == "workspace_execute":
        return "workspace"
    return "self"
