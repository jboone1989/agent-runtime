from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agent_runtime.application import ApplicationRuntime
from agent_runtime.capabilities.inventory import CapabilityInventory
from agent_runtime.capabilities.model import SideEffectLevel
from agent_runtime.integrations.scheduler import (
    APSchedulerTaskProvider,
    TASK_CANCEL,
    TASK_LIST,
    TASK_SCHEDULE,
    TaskSchedulePolicy,
    register_task_scheduler_provider,
)


class _FakeScheduler:
    def __init__(self):
        self.jobs = {}
        self.started = False
        self.stopped = False

    def add_job(self, func, **kwargs):
        self.jobs[kwargs["id"]] = {"func": func, **kwargs}

    def remove_job(self, job_id):
        if job_id not in self.jobs:
            raise KeyError(job_id)
        del self.jobs[job_id]

    def start(self):
        self.started = True

    def shutdown(self, wait=False):
        self.stopped = True


class _Clock:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


def _provider(tmp_path, dispatched, clock=None, scheduler=None, **policy_kwargs):
    scheduler = scheduler or _FakeScheduler()
    provider = APSchedulerTaskProvider(
        state_path=tmp_path / "scheduler.json",
        dispatch=lambda capability_id, payload: dispatched.append((capability_id, dict(payload))),
        policy=TaskSchedulePolicy(**policy_kwargs),
        scheduler_factory=lambda: scheduler,
        wall_clock=clock,
    )
    return provider, scheduler


def test_scheduler_manifest_and_inventory_classify_authority(tmp_path) -> None:
    provider, _ = _provider(tmp_path, [])
    inventory = CapabilityInventory()
    registered = register_task_scheduler_provider(provider, inventory)

    assert set(registered) == {TASK_SCHEDULE, TASK_CANCEL, TASK_LIST}
    assert inventory.get(TASK_LIST).side_effects is SideEffectLevel.READ_ONLY
    assert inventory.get(TASK_SCHEDULE).side_effects is SideEffectLevel.LOCAL_STATE
    assert inventory.get(TASK_SCHEDULE).entry_point["required_permissions"] == ["task.manage"]
    provider.close()


def test_schedule_list_fire_and_cancel_are_bounded(tmp_path) -> None:
    dispatched = []
    now = datetime(2026, 9, 14, 9, 0, tzinfo=timezone.utc)
    provider, scheduler = _provider(tmp_path, dispatched, clock=lambda: now)

    scheduled = provider.invoke(
        TASK_SCHEDULE,
        {
            "task_id": "hourly-report",
            "target_capability": "report.generate",
            "payload": {"scope": "hourly"},
            "trigger": {"type": "interval", "seconds": 3600},
        },
    )
    listed = provider.invoke(TASK_LIST, {})

    assert scheduled["task"]["payload_sha256"]
    assert "payload" not in scheduled["task"]
    assert listed["count"] == 1
    scheduler.jobs["hourly-report"]["func"]("hourly-report")
    assert dispatched == [("report.generate", {"scope": "hourly"})]
    assert provider.invoke(TASK_LIST, {})["tasks"][0]["run_count"] == 1

    cancelled = provider.invoke(TASK_CANCEL, {"task_id": "hourly-report"})
    assert cancelled["task_id"] == "hourly-report"
    assert provider.invoke(TASK_LIST, {})["count"] == 0
    provider.close()


def test_scheduler_state_restores_recurring_jobs_without_replaying_expired_dates(tmp_path) -> None:
    now = datetime(2026, 9, 14, 9, 0, tzinfo=timezone.utc)
    clock = _Clock(now)
    first_scheduler = _FakeScheduler()
    first, _ = _provider(tmp_path, [], clock=clock, scheduler=first_scheduler)
    first.invoke(
        TASK_SCHEDULE,
        {
            "task_id": "recurring",
            "target_capability": "observation.clock",
            "trigger": {"type": "interval", "seconds": 60},
        },
    )
    first.invoke(
        TASK_SCHEDULE,
        {
            "task_id": "one-shot",
            "target_capability": "observation.clock",
            "trigger": {"type": "date", "run_at": (now + timedelta(minutes=1)).isoformat()},
        },
    )
    first.close()

    clock.value = now + timedelta(minutes=2)
    restarted_scheduler = _FakeScheduler()
    restarted, _ = _provider(tmp_path, [], clock=clock, scheduler=restarted_scheduler)
    listed = restarted.invoke(TASK_LIST, {})

    assert [row["task_id"] for row in listed["tasks"]] == ["recurring"]
    assert set(restarted_scheduler.jobs) == {"recurring"}
    restarted.close()


def test_application_runtime_enforces_task_manage_permission(tmp_path) -> None:
    denied_provider, _ = _provider(tmp_path / "denied", [])
    denied = ApplicationRuntime.bootstrap(
        tmp_path / "denied-runtime",
        subject_id="subject",
        max_side_effect_level="LOCAL_STATE",
    )
    denied.register_provider(denied_provider)

    with pytest.raises(PermissionError, match="missing capability permissions"):
        denied.invoke(
            TASK_SCHEDULE,
            {
                "task_id": "x",
                "target_capability": "observation.clock",
                "trigger": {"type": "interval", "seconds": 60},
            },
        )

    allowed_provider, _ = _provider(tmp_path / "allowed", [])
    allowed = ApplicationRuntime.bootstrap(
        tmp_path / "allowed-runtime",
        subject_id="subject",
        max_side_effect_level="LOCAL_STATE",
        allowed_permissions=frozenset({"task.manage"}),
    )
    allowed.register_provider(allowed_provider)
    receipt = allowed.invoke(
        TASK_SCHEDULE,
        {
            "task_id": "x",
            "target_capability": "observation.clock",
            "trigger": {"type": "interval", "seconds": 60},
        },
    )
    assert receipt.payload["ok"] is True
    denied_provider.close()
    allowed_provider.close()


def test_scheduler_rejects_unbounded_payload_self_mutation_and_job_overflow(tmp_path) -> None:
    provider, _ = _provider(tmp_path, [], max_jobs=1, max_payload_bytes=16)

    with pytest.raises(ValueError, match="self-mutation"):
        provider.invoke(
            TASK_SCHEDULE,
            {"task_id": "self", "target_capability": TASK_CANCEL, "trigger": {"type": "interval", "seconds": 60}},
        )
    with pytest.raises(ValueError, match="byte limit"):
        provider.invoke(
            TASK_SCHEDULE,
            {
                "task_id": "large",
                "target_capability": "demo.run",
                "payload": {"value": "x" * 100},
                "trigger": {"type": "interval", "seconds": 60},
            },
        )

    provider.invoke(
        TASK_SCHEDULE,
        {"task_id": "one", "target_capability": "demo.run", "trigger": {"type": "interval", "seconds": 60}},
    )
    with pytest.raises(RuntimeError, match="job limit"):
        provider.invoke(
            TASK_SCHEDULE,
            {"task_id": "two", "target_capability": "demo.run", "trigger": {"type": "interval", "seconds": 60}},
        )
    provider.close()
