"""Deterministic executive. No LLM. Does not authorize or acquire capabilities."""
from __future__ import annotations

from ..experience.refs import stable_ref
from .context import ExecutiveContext
from .interruption import should_interrupt
from .records import ExecutiveDecision


class ExecutiveController:
    def deliberate(self, ctx: ExecutiveContext) -> ExecutiveDecision:
        plan = ctx.plan
        selected_goals = list(ctx.working_context.active_goal_ids)
        selected_step = None if plan is None else plan.current_step_id
        evidence = [] if ctx.event is None else [ctx.event.event_id]
        evidence.extend(f"cmt:{cid}" for cid in ctx.working_context.commitment_ids)
        missing = _missing_capability(ctx)

        if should_interrupt(plan=plan, motivation=ctx.motivation, event=ctx.event):
            return _decision(
                ctx,
                kind="interrupt",
                continue_plan=False,
                interrupt_plan=True,
                request_replan=False,
                selected_goals=selected_goals,
                selected_step=selected_step,
                reasons=["unresolved high-severity threat"],
                evidence=evidence,
            )
        if missing is not None:
            return _decision(
                ctx,
                kind="replan",
                continue_plan=False,
                interrupt_plan=False,
                request_replan=True,
                selected_goals=selected_goals,
                selected_step=selected_step,
                reasons=["capability missing", missing],
                evidence=evidence,
            )
        reason = "continue current plan" if plan is not None else "no plan"
        return _decision(
            ctx,
            kind="continue",
            continue_plan=True,
            interrupt_plan=False,
            request_replan=False,
            selected_goals=selected_goals,
            selected_step=selected_step,
            reasons=[reason],
            evidence=evidence,
        )


def _missing_capability(ctx: ExecutiveContext) -> str | None:
    plan = ctx.plan
    if plan is None or plan.current_step is None:
        return None
    usable = set(ctx.usable_capability_ids)
    for req in plan.current_step.required_capabilities:
        if req.capability_id not in usable:
            return req.capability_id
    return None


def _decision(
    ctx: ExecutiveContext,
    *,
    kind: str,
    continue_plan: bool,
    interrupt_plan: bool,
    request_replan: bool,
    selected_goals: list[str],
    selected_step: str | None,
    reasons: list[str],
    evidence: list[str],
) -> ExecutiveDecision:
    return ExecutiveDecision(
        decision_id=stable_ref(
            "exd", ctx.agent_id, ctx.logical_cycle, kind, selected_step, *reasons
        ),
        continue_plan=continue_plan,
        interrupt_plan=interrupt_plan,
        request_replan=request_replan,
        selected_goal_ids=selected_goals,
        selected_step_id=selected_step,
        reasons=reasons,
        evidence_refs=evidence,
        logical_cycle=ctx.logical_cycle,
        timestamp=ctx.timestamp,
    )
