"""Bounded filesystem event observation backed by watchdog.

The extension is intentionally restart-local: it observes events that happen after
this provider instance starts. Runtime does not pretend filesystem notifications
are a durable event log.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import threading
import time
from typing import Any
import uuid

from .contract import ObservationFailure
from .provider import FILESYSTEM_EVENTS_OBSERVE, _BoundRoot


_EVENT_TYPES = frozenset({"created", "modified", "deleted", "moved"})
_MAX_PATH_CHARS = 4096


@dataclass(frozen=True)
class FilesystemEventPolicy:
    max_queue: int = 512
    max_events_per_poll: int = 128
    debounce_ms: int = 100
    event_types: frozenset[str] = _EVENT_TYPES
    recursive: bool = True

    def __post_init__(self) -> None:
        if not 1 <= int(self.max_queue) <= 10_000:
            raise ValueError("max_queue must be in [1, 10000]")
        if not 1 <= int(self.max_events_per_poll) <= int(self.max_queue):
            raise ValueError("max_events_per_poll must be positive and no larger than max_queue")
        if not 0 <= int(self.debounce_ms) <= 60_000:
            raise ValueError("debounce_ms must be in [0, 60000]")
        normalized = frozenset(str(item).lower() for item in self.event_types)
        if not normalized or not normalized <= _EVENT_TYPES:
            raise ValueError("event_types contains an unsupported filesystem event")
        object.__setattr__(self, "event_types", normalized)


@dataclass
class _QueuedEvent:
    payload: dict[str, Any]
    key: tuple[Any, ...]
    monotonic_ns: int


class _WatchdogHandler:
    """Minimal watchdog-compatible handler without importing watchdog at module load."""

    def __init__(self, callback: Callable[[Any], None]) -> None:
        self._callback = callback

    def dispatch(self, event: Any) -> None:
        self._callback(event)


class WatchdogFilesystemEvents:
    capability_id = FILESYSTEM_EVENTS_OBSERVE

    def __init__(
        self,
        *,
        roots: Mapping[str, str | Path],
        policy: FilesystemEventPolicy | None = None,
        observer_factory: Callable[[], Any] | None = None,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if not roots:
            raise ValueError("at least one filesystem event root is required")
        if any(not isinstance(key, str) or not key for key in roots):
            raise ValueError("filesystem event root identifiers must be non-empty strings")
        self.policy = policy or FilesystemEventPolicy()
        self._roots = {
            key: _BoundRoot(value, kind="filesystem-event") for key, value in roots.items()
        }
        self._ordered_root_ids = tuple(
            sorted(self._roots, key=lambda key: len(str(self._roots[key].path)), reverse=True)
        )
        self._monotonic_ns = monotonic_ns
        self._wall_clock = wall_clock
        self._queue: deque[_QueuedEvent] = deque()
        self._lock = threading.RLock()
        self._dropped = 0
        self._coalesced = 0
        self._closed = False
        started = self._wall_clock()
        if started.tzinfo is None or started.utcoffset() is None:
            raise ValueError("filesystem event wall_clock must return timezone-aware datetimes")
        self._started_at = started.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
        self._stream_epoch = f"filesystem-events:v1:{uuid.uuid4().hex}"
        self._observer = (observer_factory or _default_observer_factory)()
        self._handler = _WatchdogHandler(self._on_event)
        for root in self._roots.values():
            self._observer.schedule(
                self._handler,
                str(root.path),
                recursive=bool(self.policy.recursive),
            )
        self._observer.start()

    def observe(self, payload: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        if self._closed:
            raise ObservationFailure("provider_closed", "filesystem event observer is closed")
        if not isinstance(payload, Mapping):
            raise ObservationFailure("invalid_request", "filesystem event payload must be an object")
        root_id = payload.get("root_id")
        if root_id is not None and (not isinstance(root_id, str) or root_id not in self._roots):
            raise ObservationFailure("unknown_root", "filesystem event root_id is not configured")
        requested_types = payload.get("event_types")
        if requested_types is None:
            event_types = self.policy.event_types
        else:
            if not isinstance(requested_types, list) or not requested_types:
                raise ObservationFailure("invalid_event_types", "event_types must be a non-empty list")
            event_types = frozenset(str(item).lower() for item in requested_types)
            if not event_types <= self.policy.event_types:
                raise ObservationFailure("invalid_event_types", "requested event type is outside the configured policy")
        max_events = payload.get("max_events", self.policy.max_events_per_poll)
        if (
            isinstance(max_events, bool)
            or not isinstance(max_events, int)
            or not 1 <= max_events <= self.policy.max_events_per_poll
        ):
            raise ObservationFailure("invalid_max_events", "requested max_events exceeds the configured bound")
        for root in self._roots.values():
            root.check()

        selected: list[dict[str, Any]] = []
        with self._lock:
            retained: deque[_QueuedEvent] = deque()
            while self._queue:
                item = self._queue.popleft()
                row = item.payload
                matches = (
                    row["event_type"] in event_types
                    and (root_id is None or row["root_id"] == root_id)
                    and len(selected) < max_events
                )
                if matches:
                    selected.append(dict(row))
                else:
                    retained.append(item)
            self._queue = retained
            dropped = self._dropped
            coalesced = self._coalesced
            self._dropped = 0
            self._coalesced = 0
            remaining = len(self._queue)

        return (
            {
                "stream_epoch": self._stream_epoch,
                "started_at": self._started_at,
                "restart_semantics": "events_since_provider_start_only",
                "events": selected,
                "event_count": len(selected),
                "remaining_queue": remaining,
            },
            {
                "library": "watchdog",
                "recursive": bool(self.policy.recursive),
                "max_queue": self.policy.max_queue,
                "max_events_per_poll": self.policy.max_events_per_poll,
                "debounce_ms": self.policy.debounce_ms,
                "dropped_events_since_last_poll": dropped,
                "coalesced_events_since_last_poll": coalesced,
            },
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._observer.stop()
        finally:
            try:
                self._observer.join(timeout=2.0)
            finally:
                for root in self._roots.values():
                    root.close()

    def _on_event(self, event: Any) -> None:
        if self._closed:
            return
        event_type = str(getattr(event, "event_type", "")).lower()
        if event_type not in self.policy.event_types:
            return
        located = self._locate(getattr(event, "src_path", None), allow_missing_leaf=event_type in {"deleted", "moved"})
        if located is None:
            return
        root_id, path = located
        dest_path = None
        if event_type == "moved":
            destination = self._locate(getattr(event, "dest_path", None), allow_missing_leaf=True)
            if destination is None or destination[0] != root_id:
                return
            dest_path = destination[1]
        is_directory = bool(getattr(event, "is_directory", False))
        now_ns = int(self._monotonic_ns())
        observed = self._wall_clock()
        if observed.tzinfo is None or observed.utcoffset() is None:
            return
        row = {
            "root_id": root_id,
            "event_type": event_type,
            "path": path,
            "is_directory": is_directory,
            "observed_at": observed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"),
        }
        if dest_path is not None:
            row["dest_path"] = dest_path
        key = (root_id, event_type, path, dest_path, is_directory)
        debounce_ns = int(self.policy.debounce_ms) * 1_000_000
        with self._lock:
            if debounce_ns:
                for index in range(len(self._queue) - 1, -1, -1):
                    prior = self._queue[index]
                    if now_ns - prior.monotonic_ns > debounce_ns:
                        break
                    if prior.key == key:
                        row["coalesced_count"] = int(prior.payload.get("coalesced_count", 1)) + 1
                        self._queue[index] = _QueuedEvent(row, key, now_ns)
                        self._coalesced += 1
                        return
            if len(self._queue) >= self.policy.max_queue:
                self._queue.popleft()
                self._dropped += 1
            row["coalesced_count"] = 1
            self._queue.append(_QueuedEvent(row, key, now_ns))

    def _locate(self, raw_path: Any, *, allow_missing_leaf: bool) -> tuple[str, str] | None:
        if not isinstance(raw_path, (str, bytes, os.PathLike)):
            return None
        absolute = Path(os.path.abspath(os.fsdecode(os.fspath(raw_path))))
        for root_id in self._ordered_root_ids:
            root = self._roots[root_id]
            try:
                relative = absolute.relative_to(root.path)
            except ValueError:
                continue
            rendered = relative.as_posix() if relative.parts else "."
            if len(rendered) > _MAX_PATH_CHARS:
                return None
            try:
                root.child("" if rendered == "." else rendered, allow_missing_leaf=allow_missing_leaf)
            except ObservationFailure:
                return None
            return root_id, rendered
        return None


def _default_observer_factory() -> Any:
    try:
        from watchdog.observers import Observer
    except ImportError as exc:
        raise RuntimeError(
            "filesystem events extra missing; install agent-runtime[filesystem-events]"
        ) from exc
    return Observer()


__all__ = ["FilesystemEventPolicy", "WatchdogFilesystemEvents"]
