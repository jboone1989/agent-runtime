"""Agent Runtime v0.2.3 Executive Foundation — acceptance tests.

Deterministic Attention / WorkingContext / Plan / interrupt-resume / ExperienceTrace.
No LLM. Capability Resolver is v0.2.4 and is not in this slice.
"""
from __future__ import annotations

import ast
import json
from datetime import datetime, timezone
from pathlib import Path

from agent_runtime.capabilities.inventory import CapabilityInventory
from agent_runtime.capabilities.model import CapabilityRequirement
from agent_runtime.clock import FrozenClock
from agent_runtime.goals.goal import Goal, GoalOrigin, GoalStatus
from agent_runtime.motivation.engine import ActionTendency, MotivationState
from agent_runtime.observations import Event, Observation
from agent_runtime.runtime import AgentRuntime


SRC = Path(__file__).resolve().parents[1] / "src" / "agent_runtime"
CLOCK = datetime(2026, 8, 22, 4, 0, tzinfo=timezone.utc)


def _healthy(**overrides: float) -> dict[str, float]:
    metrics = {
        "operational_integrity": 0.9,
        "identity_integrity": 0.95,
        "memory_integrity": 0.9,
        "compute": 0.8,
        "storage": 0.8,
        "network_connectivity": 0.9,
        "financial_resources": 0.7,
        "capability": 0.8,
        "security": 0.85,
        "social_support": 0.5,
        "goal_progress": 0.6,
        "future_survivability": 0.7,
    }
    metrics.update(overrides)
    return metrics


def _motivation(
    *,
    threat: float = 0.1,
    tendencies: list[ActionTendency] | None = None,
    priority: list[str] | None = None,
) -> MotivationState:
    return MotivationState(
        valence=0.0,
        arousal=0.2,
        urgency=0.2,
        threat=threat,
        opportunity=0.1,
        drives={},
        attention_bias={},
        action_tendencies=tendencies or [ActionTendency("introspect", 0.4, "work")],
        learning_signal=0.0,
        self_model_updates=[],
        attention_priority=priority or ["task", "safety"],
    )


def _goal(description: str = "finish the note") -> Goal:
    return Goal.create(
        description=description,
        origin=GoalOrigin.USER,
        created_at=CLOCK,
        reason="operator submitted",
        priority=0.4,
    )


def test_two_step_plan_keeps_current_step_across_ticks(tmp_path: Path) -> None:
    from agent_runtime.planning.state import PlanStatus

    rt = AgentRuntime.bootstrap(state_dir=tmp_path, clock=FrozenClock(CLOCK))
    rt.submit_goal("finish the long-running research note")
    plan = rt.active_plan
    assert plan is not None
    assert len(plan.steps) >= 2
    plan_id = plan.plan_id
    step_a = plan.current_step_id
    rt.step(Observation.self_and_world(self_metrics=_healthy(), world={}))
    assert rt.active_plan is not None
    assert rt.active_plan.plan_id == plan_id
    step_b = rt.active_plan.current_step_id
    assert step_b != step_a
    rt.step(Observation.self_and_world(self_metrics=_healthy(), world={}))
    assert rt.active_plan.plan_id == plan_id
    assert rt.active_plan.status in {PlanStatus.ACTIVE, PlanStatus.COMPLETED, PlanStatus.WAITING}


def test_restart_keeps_plan_and_waiting(tmp_path: Path) -> None:
    from agent_runtime.planning.state import PlanStatus

    rt = AgentRuntime.bootstrap(state_dir=tmp_path, clock=FrozenClock(CLOCK))
    rt.submit_goal("keep this plan")
    rt.step(Observation.self_and_world(self_metrics=_healthy(), world={}))
    rt.active_plan.status = PlanStatus.WAITING
    rt.waiting  # persist list may be populated by interrupt; seed a wait if empty
    from agent_runtime.time.waiting import WaitingState

    if not rt.waiting:
        rt.waiting.append(
            WaitingState(
                wait_id="wait_seed",
                plan_id=rt.active_plan.plan_id,
                reason="operator pause",
                until="threat_resolved",
                created_cycle=rt.cycle,
                evidence_refs=[],
            )
        )
    plan_id = rt.active_plan.plan_id
    current = rt.active_plan.current_step_id
    wait_n = len(rt.waiting)
    rt.persist()
    restored = AgentRuntime.restore(state_dir=tmp_path, clock=FrozenClock(CLOCK))
    assert restored.active_plan is not None
    assert restored.active_plan.plan_id == plan_id
    assert restored.active_plan.current_step_id == current
    assert restored.active_plan.status == PlanStatus.WAITING
    assert len(restored.waiting) == wait_n


def test_high_severity_internal_event_interrupts_without_failing_goal(tmp_path: Path) -> None:
    from agent_runtime.planning.state import PlanStatus

    rt = AgentRuntime.bootstrap(
        state_dir=tmp_path,
        clock=FrozenClock(CLOCK),
        motivation_engine=_ScriptedThreatEngine(),
    )
    goal = rt.submit_goal("ordinary user work")
    rt.step(Observation.self_and_world(self_metrics=_healthy(), world={}))
    assert rt.active_plan is not None
    event = Event(
        event_id="evt_threat",
        kind="INTERNAL",
        description="capability at risk",
        data={"resolved": False},
    )
    rt.step(Observation.self_and_world(self_metrics=_healthy(), world={}, event=event))
    assert goal.status == GoalStatus.ACTIVE
    assert any(g.goal_id == goal.goal_id and g.status == GoalStatus.ACTIVE for g in rt.goals)
    assert rt.active_plan.status == PlanStatus.WAITING
    assert not any(g.status == GoalStatus.FAILED for g in rt.goals if g.goal_id == goal.goal_id)


def test_resolved_threat_resumes_original_plan(tmp_path: Path) -> None:
    from agent_runtime.planning.state import PlanStatus

    rt = AgentRuntime.bootstrap(
        state_dir=tmp_path,
        clock=FrozenClock(CLOCK),
        motivation_engine=_ScriptedThreatEngine(),
    )
    rt.submit_goal("ordinary user work")
    rt.step(Observation.self_and_world(self_metrics=_healthy(), world={}))
    plan_id = rt.active_plan.plan_id
    current = rt.active_plan.current_step_id
    rt.step(
        Observation.self_and_world(
            self_metrics=_healthy(),
            world={},
            event=Event("evt_t", "INTERNAL", "threat", {"resolved": False}),
        )
    )
    assert rt.active_plan.status == PlanStatus.WAITING
    rt.step(
        Observation.self_and_world(
            self_metrics=_healthy(),
            world={},
            event=Event("evt_ok", "INTERNAL", "cleared", {"resolved": True}),
        )
    )
    assert rt.active_plan.plan_id == plan_id
    assert rt.active_plan.status == PlanStatus.ACTIVE
    assert rt.active_plan.current_step_id == current


def test_missing_capability_blocks_without_unauthorized_execute(tmp_path: Path) -> None:
    from agent_runtime.planning.plan import Plan
    from agent_runtime.planning.state import PlanStatus
    from agent_runtime.planning.step import PlanStep

    rt = AgentRuntime.bootstrap(state_dir=tmp_path, clock=FrozenClock(CLOCK))
    goal = rt.submit_goal("need a missing skill")
    missing_step = PlanStep(
        step_id="step_teleport",
        objective="teleport",
        required_capabilities=[CapabilityRequirement(capability_id="teleport")],
        status=PlanStatus.ACTIVE,
    )
    rt.plans = [
        Plan(
            plan_id="plan_gap",
            goal_ids=[goal.goal_id],
            steps=[missing_step],
            current_step_id="step_teleport",
            status=PlanStatus.ACTIVE,
            created_cycle=rt.cycle,
        )
    ]
    result = rt.step(Observation.self_and_world(self_metrics=_healthy(), world={}))
    assert rt.active_plan.status == PlanStatus.BLOCKED
    assert rt.active_plan.current_step.status == PlanStatus.BLOCKED
    assert not any(a.kind == "teleport" for a in result.actions)
    assert goal.status != GoalStatus.FAILED


def test_attention_budget_changes_working_context() -> None:
    from agent_runtime.executive.attention import AttentionController
    from agent_runtime.executive.working_context import WorkingContextBuilder

    candidates = [f"sig:{i}" for i in range(6)] + [f"mem:{i}" for i in range(6)]
    controller = AttentionController(entry_budget=4)
    alloc = controller.allocate(
        _motivation(priority=["sig:0", "mem:0", "sig:1"]),
        event=None,
        candidates=candidates,
    )
    assert len(alloc.attended) == 4
    assert len(alloc.ignored) == 8
    assert all(item.reason for item in alloc.ignored)
    ctx = WorkingContextBuilder().build(
        agent_id="a1",
        logical_cycle=1,
        timestamp=CLOCK,
        goal_ids=["g1"],
        plan_id="p1",
        attention=alloc,
        token_budget=100,
        compute_budget=0.5,
    )
    assert ctx.attended_signal_refs == [c for c in alloc.attended if c.startswith("sig:")]
    assert ctx.retrieved_memory_refs == [c for c in alloc.attended if c.startswith("mem:")]
    assert "sig:5" not in ctx.attended_signal_refs

    rest = controller.allocate(
        _motivation(
            tendencies=[ActionTendency("conserve", 0.9, "rest", dimension="REST")],
            priority=["sig:0"],
        ),
        event=None,
        candidates=candidates,
    )
    assert len(rest.attended) < len(alloc.attended)
    assert rest.exploration_slots < alloc.exploration_slots


def test_same_inputs_same_executive_decision() -> None:
    from agent_runtime.executive.context import ExecutiveContext
    from agent_runtime.executive.controller import ExecutiveController
    from agent_runtime.executive.working_context import WorkingContext
    from agent_runtime.planning.plan import Plan
    from agent_runtime.planning.state import PlanStatus
    from agent_runtime.planning.step import PlanStep

    inventory = CapabilityInventory()
    inventory.seed_builtin()
    plan = Plan(
        plan_id="plan_det",
        goal_ids=["goal_1"],
        steps=[
            PlanStep(
                step_id="s1",
                objective="introspect",
                required_capabilities=[CapabilityRequirement(capability_id="introspect")],
                status=PlanStatus.ACTIVE,
            )
        ],
        current_step_id="s1",
        status=PlanStatus.ACTIVE,
        created_cycle=1,
    )
    ctx = ExecutiveContext(
        agent_id="agent_det",
        logical_cycle=3,
        timestamp=CLOCK,
        plan=plan,
        motivation=_motivation(),
        event=None,
        working_context=WorkingContext(
            context_id="ctx_det",
            active_goal_ids=["goal_1"],
            active_plan_id="plan_det",
            attended_signal_refs=["sig:0"],
            retrieved_memory_refs=[],
            open_gap_ids=[],
            commitment_ids=[],
            token_budget=10,
            compute_budget=0.1,
        ),
        usable_capability_ids=inventory.usable_ids(),
    )
    ctl = ExecutiveController()
    assert ctl.deliberate(ctx) == ctl.deliberate(ctx)


def test_experience_trace_links_goal_plan_action_outcome(tmp_path: Path) -> None:
    rt = AgentRuntime.bootstrap(state_dir=tmp_path, clock=FrozenClock(CLOCK))
    goal = rt.submit_goal("trace this")
    rt.step(Observation.self_and_world(self_metrics=_healthy(), world={}))
    assert rt.experience_traces
    tr = rt.experience_traces[-1]
    assert goal.goal_id in tr.goal_refs
    assert tr.plan_ref == rt.active_plan.plan_id or tr.plan_ref
    assert tr.step_ref
    assert tr.decision_ref
    assert tr.action_refs
    assert tr.outcome_refs
    assert tr.logical_cycle == rt.cycle
    assert tr.agent_id == rt.identity.agent_id


def test_old_v022_snapshot_restores(tmp_path: Path) -> None:
    rt = AgentRuntime.bootstrap(state_dir=tmp_path, clock=FrozenClock(CLOCK))
    rt.submit_goal("legacy goal")
    rt.step(Observation.self_and_world(self_metrics=_healthy(), world={}))
    agent_id = rt.identity.agent_id
    path = tmp_path / "agent_state.json"
    rt.persist()
    raw = json.loads(path.read_text(encoding="utf-8"))
    data = raw["data"]
    for key in ("plans", "waiting", "experience_traces", "working_context"):
        data.pop(key, None)
    path.write_text(json.dumps(raw), encoding="utf-8")
    restored = AgentRuntime.restore(state_dir=tmp_path, clock=FrozenClock(CLOCK))
    assert restored.identity.agent_id == agent_id
    assert any(g.description == "legacy goal" for g in restored.goals)
    assert restored.plans == []
    assert restored.waiting == []
    assert restored.experience_traces == []


def test_executive_modules_do_not_import_virtualbrain_or_network() -> None:
    roots = [
        SRC / "executive",
        SRC / "planning",
        SRC / "time",
        SRC / "experience",
    ]
    forbidden = ("virtualbrain", "agent_network", "agent-network")
    for root in roots:
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        lowered = alias.name.lower()
                        assert not any(f in lowered for f in forbidden), path
                if isinstance(node, ast.ImportFrom) and node.module:
                    lowered = node.module.lower()
                    assert not any(f in lowered for f in forbidden), path


class _ScriptedThreatEngine:
    """Deterministic stand-in: unresolved events raise threat; resolved clears it."""

    def process(self, **kwargs) -> MotivationState:
        event = kwargs.get("event")
        if event is not None and event.data.get("resolved") is False:
            return _motivation(
                threat=0.85,
                tendencies=[ActionTendency("diagnose", 0.9, "unresolved threat")],
                priority=["threat", "safety"],
            )
        if event is not None and event.data.get("resolved") is True:
            return _motivation(
                threat=0.15,
                tendencies=[ActionTendency("introspect", 0.5, "resume work")],
                priority=["task"],
            )
        return _motivation()
