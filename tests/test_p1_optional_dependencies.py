from __future__ import annotations

from datetime import datetime, timezone


def test_p1_optional_dependencies_import_and_initialize(tmp_path) -> None:
    from apscheduler.schedulers.background import BackgroundScheduler
    from playwright.sync_api import sync_playwright
    from sqlalchemy import create_engine
    from watchdog.observers import Observer

    assert BackgroundScheduler is not None
    assert create_engine is not None
    assert Observer is not None

    playwright = sync_playwright().start()
    try:
        assert playwright.chromium.name == "chromium"
    finally:
        playwright.stop()


def test_real_watchdog_observer_lifecycle(tmp_path) -> None:
    from agent_runtime.observation import WatchdogFilesystemEvents

    root = tmp_path / "watch"
    root.mkdir()
    extension = WatchdogFilesystemEvents(roots={"root": root})
    extension.close()


def test_real_apscheduler_provider_lifecycle(tmp_path) -> None:
    from agent_runtime.integrations.scheduler import (
        APSchedulerTaskProvider,
        TASK_LIST,
        TASK_SCHEDULE,
    )

    provider = APSchedulerTaskProvider(
        state_path=tmp_path / "tasks.json",
        dispatch=lambda capability_id, payload: None,
        wall_clock=lambda: datetime(2026, 9, 14, 9, 0, tzinfo=timezone.utc),
    )
    provider.invoke(
        TASK_SCHEDULE,
        {
            "task_id": "future",
            "target_capability": "observation.clock",
            "trigger": {"type": "interval", "seconds": 3600},
        },
    )
    assert provider.invoke(TASK_LIST, {})["count"] == 1
    provider.close()
