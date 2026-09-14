"""Provider-neutral local task scheduling backed by APScheduler 3.x."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import threading
from typing import Any

from ..capabilities.inventory import CapabilityInventory
from ..capabilities.model import (
    CapabilityAccessMode,
    CapabilityBinding,
    CapabilityKind,
    CapabilityLifecycleState,
    SideEffectLevel,
)

TASK_SCHEDULE = "task.schedule"
TASK_CANCEL = "task.cancel"
TASK_LIST = "task.list"
_TASK_CAPABILITIES = (TASK_SCHEDULE, TASK_CANCEL, TASK_LIST)
_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_CRON_FIELDS = frozenset({"year", "month", "day", "week", "day_of_week", "hour", "minute", "second"})


@dataclass(frozen=True)
class TaskSchedulePolicy:
    max_jobs: int = 128
    max_payload_bytes: int = 64 * 1024
    min_interval_seconds: int = 1
    max_interval_seconds: int = 365 * 24 * 60 * 60

    def __post_init__(self) -> None:
        if not 1 <= int(self.max_jobs) <= 10_000:
            raise ValueError("max_jobs must be in [1, 10000]")
        if not 1 <= int(self.max_payload_bytes) <= 1_048_576:
            raise ValueError("max_payload_bytes must be in [1, 1048576]")
        if not 1 <= int(self.min_interval_seconds) <= int(self.max_interval_seconds):
            raise ValueError("invalid interval bounds")
        if int(self.max_interval_seconds) > 10 * 365 * 24 * 60 * 60:
            raise ValueError("max_interval_seconds exceeds hard ceiling")


class APSchedulerTaskProvider:
    provider_name = "apscheduler"
    contract_version = "runtime-task-schedule:v1"

    def __init__(
        self,
        *,
        state_path: str | Path,
        dispatch: Callable[[str, Mapping[str, Any]], Any],
        policy: TaskSchedulePolicy | None = None,
        scheduler_factory: Callable[[], Any] | None = None,
        wall_clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not callable(dispatch):
            raise TypeError("dispatch must be callable")
        self.policy = policy or TaskSchedulePolicy()
        self.state_path = Path(state_path)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self._dispatch = dispatch
        self._wall_clock = wall_clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.RLock()
        self._closed = False
        self._records = self._load_state()
        self._scheduler = scheduler_factory() if scheduler_factory else _load_scheduler()
        self._restore_jobs()
        self._scheduler.start()

    def manifest(self) -> dict[str, Any]:
        rows = []
        for capability_id in _TASK_CAPABILITIES:
            mutating = capability_id in {TASK_SCHEDULE, TASK_CANCEL}
            rows.append(
                {
                    "capability_id": capability_id,
                    "provider": self.provider_name,
                    "version": self.contract_version,
                    "side_effect_level": "LOCAL_STATE" if mutating else "READ_ONLY",
                    "required_permissions": ["task.manage"] if mutating else [],
                }
            )
        return {"provider": self.provider_name, "version": self.contract_version, "capabilities": rows}

    def invoke(self, capability_id: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if self._closed:
            raise RuntimeError("task scheduler provider is closed")
        request = dict(payload or {})
        if capability_id == TASK_SCHEDULE:
            return self._schedule(request)
        if capability_id == TASK_CANCEL:
            return self._cancel(request)
        if capability_id == TASK_LIST:
            return self._list(request)
        raise ValueError(f"unsupported task capability: {capability_id}")

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._persist()
            try:
                self._scheduler.shutdown(wait=False)
            except Exception:
                pass

    def _schedule(self, request: dict[str, Any]) -> dict[str, Any]:
        allowed = {"task_id", "target_capability", "payload", "trigger"}
        _reject_unknown(request, allowed)
        task_id = _bounded_id(request.get("task_id"), "task_id")
        target = _bounded_id(request.get("target_capability"), "target_capability")
        if target in {TASK_SCHEDULE, TASK_CANCEL}:
            raise ValueError("scheduler self-mutation cannot be a scheduled target")
        task_payload = request.get("payload", {})
        if not isinstance(task_payload, Mapping):
            raise ValueError("scheduled payload must be an object")
        payload_json = json.dumps(task_payload, ensure_ascii=False, allow_nan=False, sort_keys=True)
        if len(payload_json.encode("utf-8")) > self.policy.max_payload_bytes:
            raise ValueError("scheduled payload exceeds configured byte limit")
        trigger = self._normalize_trigger(request.get("trigger"))
        record = {
            "task_id": task_id,
            "target_capability": target,
            "payload": json.loads(payload_json),
            "trigger": trigger,
            "created_at": _utc_text(self._now()),
            "run_count": 0,
            "last_run_at": None,
            "last_status": None,
            "last_error_type": None,
        }
        with self._lock:
            if task_id not in self._records and len(self._records) >= self.policy.max_jobs:
                raise RuntimeError("task scheduler job limit reached")
            previous = self._records.get(task_id)
            self._records[task_id] = record
            try:
                self._install_record(record, replace_existing=True)
                self._persist()
            except Exception:
                if previous is None:
                    self._records.pop(task_id, None)
                else:
                    self._records[task_id] = previous
                raise
        return {"ok": True, "provider": self.provider_name, "capability_id": TASK_SCHEDULE, "task": self._public(record)}

    def _cancel(self, request: dict[str, Any]) -> dict[str, Any]:
        _reject_unknown(request, {"task_id"})
        task_id = _bounded_id(request.get("task_id"), "task_id")
        with self._lock:
            record = self._records.pop(task_id, None)
            if record is None:
                raise KeyError("scheduled task not found")
            try:
                self._scheduler.remove_job(task_id)
            except Exception:
                pass
            self._persist()
        return {"ok": True, "provider": self.provider_name, "capability_id": TASK_CANCEL, "task_id": task_id}

    def _list(self, request: dict[str, Any]) -> dict[str, Any]:
        _reject_unknown(request, set())
        with self._lock:
            rows = [self._public(self._records[key]) for key in sorted(self._records)]
        return {
            "ok": True,
            "provider": self.provider_name,
            "capability_id": TASK_LIST,
            "tasks": rows,
            "count": len(rows),
            "restart_semantics": "future recurring schedules are restored; expired one-shot schedules are dropped",
        }

    def _fire(self, task_id: str) -> None:
        with self._lock:
            record = self._records.get(task_id)
            if record is None or self._closed:
                return
            target = record["target_capability"]
            payload = dict(record["payload"])
        status = "success"
        error_type = None
        try:
            self._dispatch(target, payload)
        except Exception as exc:
            status = "error"
            error_type = type(exc).__name__
        with self._lock:
            record = self._records.get(task_id)
            if record is None:
                return
            record["run_count"] = int(record.get("run_count", 0)) + 1
            record["last_run_at"] = _utc_text(self._now())
            record["last_status"] = status
            record["last_error_type"] = error_type
            if record["trigger"]["type"] == "date":
                self._records.pop(task_id, None)
            self._persist()

    def _restore_jobs(self) -> None:
        expired: list[str] = []
        for task_id in sorted(self._records):
            record = self._records[task_id]
            trigger = record.get("trigger", {})
            if trigger.get("type") == "date":
                run_at = _parse_datetime(trigger.get("run_at"))
                if run_at <= self._now():
                    expired.append(task_id)
                    continue
            self._install_record(record, replace_existing=True)
        if expired:
            for task_id in expired:
                self._records.pop(task_id, None)
            self._persist()

    def _install_record(self, record: Mapping[str, Any], *, replace_existing: bool) -> None:
        trigger = dict(record["trigger"])
        kind = trigger.pop("type")
        kwargs: dict[str, Any] = {
            "id": record["task_id"],
            "args": [record["task_id"]],
            "replace_existing": replace_existing,
            "coalesce": True,
            "max_instances": 1,
            "misfire_grace_time": 1,
        }
        if kind == "date":
            kwargs.update({"trigger": "date", "run_date": _parse_datetime(trigger["run_at"])})
        elif kind == "interval":
            kwargs.update({"trigger": "interval", "seconds": int(trigger["seconds"]), "timezone": timezone.utc})
        elif kind == "cron":
            kwargs.update({"trigger": "cron", "timezone": timezone.utc, **trigger})
        else:
            raise ValueError("unsupported stored task trigger")
        self._scheduler.add_job(self._fire, **kwargs)

    def _normalize_trigger(self, raw: Any) -> dict[str, Any]:
        if not isinstance(raw, Mapping):
            raise ValueError("trigger must be an object")
        trigger = dict(raw)
        kind = trigger.get("type")
        if kind == "date":
            _reject_unknown(trigger, {"type", "run_at"})
            run_at = _parse_datetime(trigger.get("run_at"))
            if run_at <= self._now():
                raise ValueError("date trigger must be in the future")
            return {"type": "date", "run_at": _utc_text(run_at)}
        if kind == "interval":
            _reject_unknown(trigger, {"type", "seconds"})
            seconds = trigger.get("seconds")
            if isinstance(seconds, bool) or not isinstance(seconds, int):
                raise ValueError("interval seconds must be an integer")
            if not self.policy.min_interval_seconds <= seconds <= self.policy.max_interval_seconds:
                raise ValueError("interval seconds are outside the configured bounds")
            return {"type": "interval", "seconds": seconds}
        if kind == "cron":
            unknown = set(trigger) - ({"type"} | _CRON_FIELDS)
            if unknown:
                raise ValueError("cron trigger contains unsupported fields")
            fields: dict[str, Any] = {}
            for key in _CRON_FIELDS:
                if key not in trigger:
                    continue
                value = trigger[key]
                if isinstance(value, bool) or not isinstance(value, (str, int)):
                    raise ValueError("cron fields must be strings or integers")
                if len(str(value)) > 64:
                    raise ValueError("cron field exceeds maximum length")
                fields[key] = value
            if not fields:
                raise ValueError("cron trigger requires at least one field")
            return {"type": "cron", **fields}
        raise ValueError("trigger type must be date, interval, or cron")

    def _load_state(self) -> dict[str, dict[str, Any]]:
        if not self.state_path.exists():
            return {}
        raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("schema_version") != 1 or not isinstance(raw.get("tasks"), list):
            raise ValueError("task scheduler state schema mismatch")
        records: dict[str, dict[str, Any]] = {}
        for item in raw["tasks"]:
            if not isinstance(item, dict):
                raise ValueError("task scheduler state contains an invalid record")
            task_id = _bounded_id(item.get("task_id"), "task_id")
            if task_id in records:
                raise ValueError("task scheduler state contains duplicate task IDs")
            records[task_id] = dict(item)
        if len(records) > self.policy.max_jobs:
            raise ValueError("task scheduler state exceeds configured job limit")
        return records

    def _persist(self) -> None:
        payload = {"schema_version": 1, "tasks": [self._records[key] for key in sorted(self._records)]}
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
        descriptor, name = tempfile.mkstemp(prefix=f".{self.state_path.name}.", dir=self.state_path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.state_path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def _public(self, record: Mapping[str, Any]) -> dict[str, Any]:
        payload_hash = hashlib.sha256(
            json.dumps(record.get("payload", {}), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return {
            "task_id": record["task_id"],
            "target_capability": record["target_capability"],
            "trigger": dict(record["trigger"]),
            "payload_sha256": payload_hash,
            "run_count": int(record.get("run_count", 0)),
            "last_run_at": record.get("last_run_at"),
            "last_status": record.get("last_status"),
            "last_error_type": record.get("last_error_type"),
        }

    def _now(self) -> datetime:
        value = self._wall_clock()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("scheduler wall clock must be timezone-aware")
        return value.astimezone(timezone.utc)


def register_task_scheduler_provider(
    provider: APSchedulerTaskProvider, inventory: CapabilityInventory
) -> tuple[str, ...]:
    registered: list[str] = []
    for row in provider.manifest()["capabilities"]:
        capability_id = str(row["capability_id"])
        if inventory.get(capability_id) is not None:
            raise ValueError(f"task capability already registered: {capability_id}")
        level = SideEffectLevel[str(row["side_effect_level"])]
        inventory.register(
            CapabilityBinding(
                capability_id=capability_id,
                kind=CapabilityKind.SERVICE,
                provider=provider.provider_name,
                candidate_id=f"{provider.provider_name}:{capability_id}",
                access_mode=CapabilityAccessMode.LOCAL,
                state=CapabilityLifecycleState.ACTIVE,
                version=provider.contract_version,
                side_effects=level,
                entry_point={
                    "adapter": "APSchedulerTaskProvider",
                    "required_permissions": list(row.get("required_permissions") or ()),
                },
                evidence_refs=["contract:runtime-task-schedule:v1"],
            )
        )
        registered.append(capability_id)
    return tuple(registered)


def _load_scheduler() -> Any:
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError as exc:
        raise RuntimeError("scheduler extra missing; install agent-runtime[scheduler]") from exc
    return BackgroundScheduler(timezone=timezone.utc)


def _parse_datetime(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("run_at must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("run_at must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("run_at must include a timezone")
    return parsed.astimezone(timezone.utc)


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _bounded_id(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValueError(f"{field} must match [A-Za-z0-9._:-]{{1,128}}")
    return value


def _reject_unknown(request: Mapping[str, Any], allowed: set[str]) -> None:
    unknown = sorted(set(request) - allowed)
    if unknown:
        raise ValueError(f"unsupported parameters: {', '.join(unknown)}")


__all__ = [
    "APSchedulerTaskProvider",
    "TASK_CANCEL",
    "TASK_LIST",
    "TASK_SCHEDULE",
    "TaskSchedulePolicy",
    "register_task_scheduler_provider",
]
