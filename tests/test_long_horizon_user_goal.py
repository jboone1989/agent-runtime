"""Long-horizon USER goals must survive plan completion.

Operator goals that express a standing final objective (the continuity plan's
final goal) must never be silently marked COMPLETED when the machinery's
two-step plan finishes. Default USER goals keep the legacy auto-complete
behavior.
"""
from __future__ import annotations

from datetime import datetime, timezone

from agent_runtime.clock import FrozenClock
from agent_runtime.goals.goal import GoalStatus
from agent_runtime.observations import Observation
from agent_runtime.planning.state import PlanStatus
from agent_runtime.runtime import AgentRuntime


CLOCK = datetime(2026, 8, 24, 13, 0, tzinfo=timezone.utc)


def _observation() -> Observation:
    return Observation.self_and_world(
        self_metrics={
            "operational_integrity": 0.9,
            "compute": 0.8,
            "memory_integrity": 0.9,
        },
        world={},
        external_task=True,
        timestamp=CLOCK,
    )


def test_long_horizon_user_goal_survives_plan_completion(tmp_path) -> None:
    rt = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(CLOCK))
    goal = rt.submit_goal(
        "standing final objective",
        reason="operator continuity plan",
        auto_complete=False,
    )
    rt.step(_observation())
    rt.step(_observation())

    assert rt.active_plan is not None
    assert rt.active_plan.status == PlanStatus.COMPLETED
    assert goal.status == GoalStatus.ACTIVE


def test_default_user_goal_still_auto_completes(tmp_path) -> None:
    rt = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(CLOCK))
    goal = rt.submit_goal("ordinary user work")
    rt.step(_observation())
    rt.step(_observation())

    assert rt.active_plan.status == PlanStatus.COMPLETED
    assert goal.status == GoalStatus.COMPLETED


def test_auto_complete_flag_persists_and_old_goals_default_true(tmp_path) -> None:
    rt = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(CLOCK))
    goal = rt.submit_goal("standing final objective", auto_complete=False)
    assert goal.auto_complete is False
    rt.persist()
    restored = AgentRuntime.restore(tmp_path, clock=FrozenClock(CLOCK))
    restored_goal = next(
        g for g in restored.goals if g.goal_id == goal.goal_id
    )
    assert restored_goal.auto_complete is False

    from agent_runtime.codec import decode

    old_json = {
        "__dc__": "agent_runtime.goals.goal.Goal",
        "data": {
            "goal_id": "goal_old",
            "description": "old goal",
            "origin": {"__enum__": "agent_runtime.goals.goal.GoalOrigin",
                       "value": "USER"},
            "status": {"__enum__": "agent_runtime.goals.goal.GoalStatus",
                       "value": "ACTIVE"},
            "created_at": {"__dt__": "2026-08-20T00:00:00+00:00"},
            "reason": "legacy",
            "priority": 0.5,
        },
    }
    old = decode(old_json)
    assert old.auto_complete is True
