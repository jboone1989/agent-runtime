from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_runtime.observation import RuntimeObservationProvider
from agent_runtime.observation.filesystem_events import (
    FilesystemEventPolicy,
    WatchdogFilesystemEvents,
)
from agent_runtime.observation.provider import FILESYSTEM_EVENTS_OBSERVE


class _Observer:
    def __init__(self) -> None:
        self.scheduled = []
        self.started = False
        self.stopped = False
        self.joined = False

    def schedule(self, handler, path, *, recursive):
        self.scheduled.append((handler, path, recursive))

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def join(self, timeout=None):
        self.joined = True


def _clock():
    return datetime(2026, 9, 14, 8, 0, tzinfo=timezone.utc)


def test_extension_observes_bounded_coalesced_events(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "note.txt"
    target.write_text("x", encoding="utf-8")
    observer = _Observer()
    ticks = iter([100, 150])
    extension = WatchdogFilesystemEvents(
        roots={"workspace": root},
        policy=FilesystemEventPolicy(max_queue=4, max_events_per_poll=2, debounce_ms=1),
        observer_factory=lambda: observer,
        monotonic_ns=lambda: next(ticks),
        wall_clock=_clock,
    )
    handler = observer.scheduled[0][0]
    event = SimpleNamespace(event_type="modified", src_path=str(target), is_directory=False)
    handler.dispatch(event)
    handler.dispatch(event)

    data, evidence = extension.observe({"max_events": 2})

    assert observer.started is True
    assert data["restart_semantics"] == "events_since_provider_start_only"
    assert data["event_count"] == 1
    assert data["events"][0]["path"] == "note.txt"
    assert data["events"][0]["coalesced_count"] == 2
    assert evidence["coalesced_events_since_last_poll"] == 1
    extension.close()
    assert observer.stopped is True
    assert observer.joined is True


def test_queue_overflow_is_reported_and_oldest_event_is_dropped(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    observer = _Observer()
    ticks = iter([1, 2, 3])
    extension = WatchdogFilesystemEvents(
        roots={"workspace": root},
        policy=FilesystemEventPolicy(max_queue=2, max_events_per_poll=2, debounce_ms=0),
        observer_factory=lambda: observer,
        monotonic_ns=lambda: next(ticks),
        wall_clock=_clock,
    )
    handler = observer.scheduled[0][0]
    for name in ("a.txt", "b.txt", "c.txt"):
        path = root / name
        path.write_text(name, encoding="utf-8")
        handler.dispatch(SimpleNamespace(event_type="created", src_path=str(path), is_directory=False))

    data, evidence = extension.observe({})

    assert [row["path"] for row in data["events"]] == ["b.txt", "c.txt"]
    assert evidence["dropped_events_since_last_poll"] == 1
    extension.close()


def test_filters_root_and_event_types_without_discarding_unmatched_events(tmp_path: Path) -> None:
    one = tmp_path / "one"
    two = tmp_path / "two"
    one.mkdir()
    two.mkdir()
    observer = _Observer()
    extension = WatchdogFilesystemEvents(
        roots={"one": one, "two": two},
        observer_factory=lambda: observer,
        monotonic_ns=iter(range(10)).__next__,
        wall_clock=_clock,
    )
    handler = observer.scheduled[0][0]
    a = one / "a.txt"
    b = two / "b.txt"
    a.write_text("a", encoding="utf-8")
    b.write_text("b", encoding="utf-8")
    handler.dispatch(SimpleNamespace(event_type="created", src_path=str(a), is_directory=False))
    handler.dispatch(SimpleNamespace(event_type="modified", src_path=str(b), is_directory=False))

    first, _ = extension.observe({"root_id": "one", "event_types": ["created"]})
    second, _ = extension.observe({})

    assert [row["path"] for row in first["events"]] == ["a.txt"]
    assert [row["path"] for row in second["events"]] == ["b.txt"]
    extension.close()


def test_redirected_event_path_is_not_exposed(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    observer = _Observer()
    extension = WatchdogFilesystemEvents(
        roots={"workspace": root},
        observer_factory=lambda: observer,
        monotonic_ns=lambda: 1,
        wall_clock=_clock,
    )
    link = root / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    observer.scheduled[0][0].dispatch(
        SimpleNamespace(event_type="created", src_path=str(link / "secret.txt"), is_directory=False)
    )

    data, _ = extension.observe({})
    assert data["events"] == []
    extension.close()


def test_runtime_provider_routes_and_closes_filesystem_extension(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    observer = _Observer()
    extension = WatchdogFilesystemEvents(
        roots={"workspace": root},
        observer_factory=lambda: observer,
        monotonic_ns=lambda: 1,
        wall_clock=_clock,
    )
    provider = RuntimeObservationProvider(extensions=(extension,))

    result = provider.invoke(FILESYSTEM_EVENTS_OBSERVE, {})
    assert result["ok"] is True
    assert result["capability_id"] == FILESYSTEM_EVENTS_OBSERVE

    provider.close()
    assert observer.stopped is True
    assert observer.joined is True
