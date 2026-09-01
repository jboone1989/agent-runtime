"""One thin provider bundle for Runtime-owned read-only observations."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
import base64
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
import math
import os
from pathlib import Path, PurePosixPath
import platform
import stat
import sys
import time
from typing import Any, Protocol
from urllib.parse import urljoin, urlsplit

from dulwich import porcelain
from dulwich.repo import Repo
import httpx
import psutil

from ..capabilities.inventory import CapabilityInventory
from ..capabilities.model import (
    CapabilityAccessMode,
    CapabilityBinding,
    CapabilityKind,
    CapabilityLifecycleState,
    SideEffectLevel,
)
from .contract import ObservationFailure, ObservationResult


GIT_OBSERVE = "observation.git"
WORKSPACE_OBSERVE = "observation.workspace"
CLOCK_OBSERVE = "observation.clock"
ENVIRONMENT_OBSERVE = "observation.environment"
SYSTEM_OBSERVE = "observation.system"
PROCESS_OBSERVE = "observation.process"
HTTP_OBSERVE = "observation.http"
FILESYSTEM_EVENTS_OBSERVE = "observation.filesystem_events"
DATABASE_READ_OBSERVE = "observation.database_read"
BUILTIN_OBSERVATION_CAPABILITIES = (
    GIT_OBSERVE,
    WORKSPACE_OBSERVE,
    CLOCK_OBSERVE,
    ENVIRONMENT_OBSERVE,
    SYSTEM_OBSERVE,
    PROCESS_OBSERVE,
    HTTP_OBSERVE,
)


class ObservationExtension(Protocol):
    """Extension point for later read-side providers such as events or SQL."""

    capability_id: str

    def observe(self, payload: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]: ...


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _path_text(value: bytes | str | os.PathLike[str]) -> str:
    if isinstance(value, bytes):
        return os.fsdecode(value)
    return os.fspath(value)


def _is_redirect(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    if stat.S_ISLNK(metadata.st_mode):
        return True
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(getattr(metadata, "st_file_attributes", 0) & reparse)


def _reject_redirected_ancestors(path: Path, *, kind: str) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if os.path.lexists(current) and _is_redirect(current):
            raise ValueError(f"{kind} root must not contain redirected path components")


class _BoundRoot:
    def __init__(self, root: str | Path, *, kind: str) -> None:
        candidate = Path(os.path.abspath(root))
        _reject_redirected_ancestors(candidate, kind=kind)
        if not candidate.is_dir() or _is_redirect(candidate):
            raise ValueError(f"{kind} root must be an existing non-redirected directory")
        self.path = candidate
        self.kind = kind
        metadata = candidate.stat(follow_symlinks=False)
        self.identity = (metadata.st_dev, metadata.st_ino, metadata.st_mode)
        self._fd: int | None = None
        self._closed = False
        directory_flag = getattr(os, "O_DIRECTORY", 0)
        nofollow_flag = getattr(os, "O_NOFOLLOW", 0)
        if directory_flag:
            try:
                self._fd = os.open(candidate, os.O_RDONLY | directory_flag | nofollow_flag)
            except OSError as exc:
                raise ValueError(f"{kind} root cannot be pinned safely") from exc

    def close(self) -> None:
        self._closed = True
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None

    def check(self) -> None:
        if self._closed:
            raise ObservationFailure("provider_closed", f"{self.kind} root is closed")
        if self._fd is not None:
            try:
                metadata = os.fstat(self._fd)
            except OSError as exc:
                raise ObservationFailure("root_unavailable", f"{self.kind} root is unavailable") from exc
            if (metadata.st_dev, metadata.st_ino, metadata.st_mode) != self.identity:
                raise ObservationFailure("root_changed", f"{self.kind} root identity changed")
            return
        if _is_redirect(self.path):
            raise ObservationFailure("root_changed", f"{self.kind} root became redirected")
        try:
            metadata = self.path.stat(follow_symlinks=False)
        except OSError as exc:
            raise ObservationFailure("root_unavailable", f"{self.kind} root is unavailable") from exc
        if (metadata.st_dev, metadata.st_ino, metadata.st_mode) != self.identity:
            raise ObservationFailure("root_changed", f"{self.kind} root identity changed")

    def child(self, raw: object = "", *, allow_missing_leaf: bool = False) -> Path:
        if not isinstance(raw, str):
            raise ObservationFailure("invalid_path", "path must be a relative string")
        logical = PurePosixPath(raw.replace("\\", "/"))
        if logical.is_absolute() or any(part in ("", ".", "..") for part in logical.parts):
            if raw not in ("", "."):
                raise ObservationFailure("path_outside_root", "path must stay within the bound root")
            logical = PurePosixPath()
        self.check()
        current = self.path
        for index, part in enumerate(logical.parts):
            current /= part
            is_leaf = index == len(logical.parts) - 1
            if not os.path.lexists(current):
                if allow_missing_leaf and is_leaf:
                    return current
                raise ObservationFailure("path_not_found", "path does not exist")
            if _is_redirect(current):
                raise ObservationFailure("redirect_blocked", "symlinks and reparse points are not followed")
        return current

    def open_relative(self, path: Path, *, directory: bool = False) -> int | None:
        """Open a resolved path relative to the pinned root on POSIX."""
        if self._closed:
            raise ObservationFailure("provider_closed", f"{self.kind} root is closed")
        if self._fd is None or not getattr(os, "O_DIRECTORY", 0):
            return None
        relative = path.relative_to(self.path)
        current_fd = os.dup(self._fd)
        try:
            parts = [part for part in relative.parts if part not in ("", ".")]
            for index, part in enumerate(parts):
                flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                if index < len(parts) - 1 or directory:
                    flags |= os.O_DIRECTORY
                next_fd = os.open(part, flags, dir_fd=current_fd)
                os.close(current_fd)
                current_fd = next_fd
            return current_fd
        except OSError:
            os.close(current_fd)
            raise


class RuntimeObservationProvider:
    """Unified provider using existing Capability Inventory and invocation gates."""

    provider_id = "runtime-observation"
    version = "1.0"

    def __init__(
        self,
        *,
        workspace_roots: Mapping[str, str | Path] | None = None,
        git_roots: Mapping[str, str | Path] | None = None,
        wall_clock: Callable[[], datetime] = _utc_now,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
        environment_allowlist: tuple[str, ...] = ("CI", "LANG", "LC_ALL", "TZ"),
        disk_path: str | Path | None = None,
        max_depth: int = 4,
        max_entries: int = 1000,
        max_bytes: int = 1_048_576,
        max_processes: int = 128,
        http_allowed_hosts: tuple[str, ...] = (),
        http_allowed_schemes: tuple[str, ...] = ("https",),
        http_timeout: float = 5.0,
        http_redirect_policy: str = "none",
        http_transport: httpx.BaseTransport | None = None,
        extensions: tuple[ObservationExtension, ...] = (),
    ) -> None:
        if not callable(wall_clock) or not callable(monotonic_ns):
            raise TypeError("observation clocks must be callable")
        for value, name in (
            (max_depth, "max_depth"),
            (max_entries, "max_entries"),
            (max_bytes, "max_bytes"),
            (max_processes, "max_processes"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if (
            isinstance(http_timeout, bool)
            or not isinstance(http_timeout, (int, float))
            or not math.isfinite(http_timeout)
            or http_timeout <= 0
        ):
            raise ValueError("http_timeout must be positive and finite")
        if http_redirect_policy not in {"none", "same-origin"}:
            raise ValueError("http_redirect_policy must be none or same-origin")
        self._workspace_roots = {
            key: _BoundRoot(value, kind="workspace")
            for key, value in (workspace_roots or {}).items()
        }
        self._git_roots = {
            key: _BoundRoot(value, kind="git") for key, value in (git_roots or {}).items()
        }
        if any(not isinstance(key, str) or not key for key in (*self._workspace_roots, *self._git_roots)):
            raise ValueError("root identifiers must be non-empty strings")
        self._wall_clock = wall_clock
        self._monotonic_ns = monotonic_ns
        self._environment_allowlist = frozenset(environment_allowlist)
        self._disk_root = _BoundRoot(disk_path or Path.cwd(), kind="disk")
        self._max_depth = max_depth
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._max_processes = max_processes
        self._http_allowed_hosts = frozenset(host.lower() for host in http_allowed_hosts)
        self._http_allowed_schemes = frozenset(scheme.lower() for scheme in http_allowed_schemes)
        self._http_timeout = float(http_timeout)
        self._http_redirect_policy = http_redirect_policy
        self._http_transport = http_transport
        extension_ids = [item.capability_id for item in extensions]
        if len(extension_ids) != len(set(extension_ids)):
            raise ValueError("observation extension capability IDs must be unique")
        self._extensions = {item.capability_id: item for item in extensions}
        if set(self._extensions) & set(BUILTIN_OBSERVATION_CAPABILITIES):
            raise ValueError("observation extension collides with a built-in capability")
        self._closed = False

    def close(self) -> None:
        self._closed = True
        for root in (*self._workspace_roots.values(), *self._git_roots.values(), self._disk_root):
            root.close()

    def __enter__(self) -> "RuntimeObservationProvider":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def manifest(self) -> dict[str, Any]:
        capability_ids = (*BUILTIN_OBSERVATION_CAPABILITIES, *sorted(self._extensions))
        return {
            "provider": self.provider_id,
            "version": self.version,
            "capabilities": [
                {
                    "capability_id": capability_id,
                    "provider": self.provider_id,
                    "version": self.version,
                    "side_effect_level": "READ_ONLY",
                }
                for capability_id in capability_ids
            ],
        }

    def invoke(
        self, capability_id: str, payload: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        request: dict[str, Any] = {}
        observed_at: datetime | None = None
        try:
            if self._closed:
                raise ObservationFailure("provider_closed", "observation provider is closed")
            if payload is not None and not isinstance(payload, Mapping):
                raise ObservationFailure("invalid_request", "observation payload must be an object")
            request = dict(payload or {})
            handler = {
                GIT_OBSERVE: self._git,
                WORKSPACE_OBSERVE: self._workspace,
                CLOCK_OBSERVE: self._clock,
                ENVIRONMENT_OBSERVE: self._environment,
                SYSTEM_OBSERVE: self._system,
                PROCESS_OBSERVE: self._process,
                HTTP_OBSERVE: self._http,
            }.get(capability_id)
            if handler is not None:
                data, evidence = handler(request, observed_at)
            elif capability_id in self._extensions:
                data, evidence = self._extensions[capability_id].observe(request)
            else:
                raise ObservationFailure("unknown_capability", "observation capability is not provided")
            if observed_at is None:
                observed_at = self._observation_time()
            result = ObservationResult(
                provider_id=self.provider_id,
                capability_id=capability_id,
                observed_at=observed_at,
                data=data,
                evidence=evidence,
            )
        except ObservationFailure as exc:
            observed_at = observed_at or self._safe_observation_time()
            result = self._failure_result(capability_id, observed_at, exc.code, exc.message, exc.evidence)
        except Exception as exc:
            observed_at = observed_at or self._safe_observation_time()
            result = ObservationResult(
                provider_id=self.provider_id,
                capability_id=capability_id,
                observed_at=observed_at,
                data={},
                evidence={},
                error={"code": "provider_error", "message": type(exc).__name__},
            )
        return result.to_dict()

    def _failure_result(
        self, capability_id: str, observed_at: datetime, code: str, message: str,
        evidence: Mapping[str, Any] | None = None,
    ) -> ObservationResult:
        safe_evidence: dict[str, Any] = {}
        if isinstance(evidence, Mapping):
            # Failure evidence is untrusted request-derived data. Keep it bounded
            # even when a provider raises before normal result validation.
            for key, value in evidence.items():
                if not isinstance(key, str):
                    continue
                text = str(key)
                if len(text.encode("utf-8")) > 1024:
                    continue
                if isinstance(value, list):
                    safe_evidence[key] = [str(item)[:1024] for item in value[:32]]
                elif isinstance(value, (str, int, float, bool)) or value is None:
                    safe_evidence[key] = value if not isinstance(value, str) else value[:1024]
        try:
            return ObservationResult(
                provider_id=self.provider_id, capability_id=capability_id,
                observed_at=observed_at, data={}, evidence=safe_evidence,
                error={"code": code, "message": message},
            )
        except (TypeError, ValueError):
            return ObservationResult(
                provider_id=self.provider_id, capability_id=capability_id,
                observed_at=self._safe_observation_time(), data={}, evidence={},
                error={"code": "provider_error", "message": "invalid failure envelope"},
            )

    def _observation_time(self) -> datetime:
        observed_at = self._wall_clock()
        if (
            not isinstance(observed_at, datetime)
            or observed_at.tzinfo is None
            or observed_at.utcoffset() is None
        ):
            raise ObservationFailure("invalid_clock", "wall clock must be timezone-aware")
        return observed_at

    def _safe_observation_time(self) -> datetime:
        try:
            return self._observation_time()
        except Exception:
            return _utc_now()

    def _select_root(self, roots: Mapping[str, _BoundRoot], payload: Mapping[str, Any]) -> tuple[str, _BoundRoot]:
        root_id = payload.get("root_id")
        if root_id is None and len(roots) == 1:
            root_id = next(iter(roots))
        if not isinstance(root_id, str) or root_id not in roots:
            raise ObservationFailure("root_not_bound", "a configured root_id is required")
        root = roots[root_id]
        root.check()
        return root_id, root

    def _clock(self, payload: Mapping[str, Any], observed_at: datetime | None) -> tuple[dict[str, Any], dict[str, Any]]:
        if payload:
            raise ObservationFailure("invalid_request", "clock observation takes no parameters")
        wall_time = self._observation_time()
        return (
            {
                "wall_time": wall_time.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"),
                "monotonic_ns": int(self._monotonic_ns()),
            },
            {"monotonic_scope": "current_process"},
        )

    def _environment(self, payload: Mapping[str, Any], _observed_at: datetime) -> tuple[dict[str, Any], dict[str, Any]]:
        names = payload.get("names", [])
        if not isinstance(names, list) or len(names) > 32 or any(not isinstance(name, str) or not name for name in names):
            raise ObservationFailure("invalid_environment_names", "names must be at most 32 non-empty strings")
        denied = sorted(set(names) - self._environment_allowlist)
        if denied:
            raise ObservationFailure("environment_name_not_allowed", "requested environment name is not allowed", evidence={"denied": denied})
        values = {name: os.environ[name] for name in names if name in os.environ}
        environment_bytes = sum(
            len(name.encode("utf-8")) + len(value.encode("utf-8"))
            for name, value in values.items()
        )
        if environment_bytes > self._max_bytes:
            raise ObservationFailure(
                "environment_too_large",
                "selected environment values exceed the configured byte limit",
                evidence={"max_bytes": self._max_bytes},
            )
        return (
            {
                "os_name": os.name,
                "platform": platform.system(),
                "platform_release": platform.release(),
                "machine": platform.machine(),
                "python": {
                    "implementation": platform.python_implementation(),
                    "version": platform.python_version(),
                    "executable": sys.executable,
                },
                "environment": values,
                "missing": sorted(set(names) - set(values)),
            },
            {"environment_allowlist_applied": True},
        )

    def _system(self, payload: Mapping[str, Any], _observed_at: datetime) -> tuple[dict[str, Any], dict[str, Any]]:
        if payload:
            raise ObservationFailure("invalid_request", "system observation takes no parameters")
        self._disk_root.check()
        memory = psutil.virtual_memory()
        swap = psutil.swap_memory()
        disk = psutil.disk_usage(str(self._disk_root.path))
        try:
            load = list(os.getloadavg())
        except (AttributeError, OSError):
            load = None
        return (
            {
                "cpu": {
                    "logical_count": psutil.cpu_count(logical=True),
                    "physical_count": psutil.cpu_count(logical=False),
                    "percent": psutil.cpu_percent(interval=None),
                    "load_average": load,
                },
                "memory": {"total": memory.total, "available": memory.available, "used": memory.used, "percent": memory.percent},
                "swap": {"total": swap.total, "used": swap.used, "free": swap.free, "percent": swap.percent},
                "disk": {"path": str(self._disk_root.path), "total": disk.total, "used": disk.used, "free": disk.free, "percent": disk.percent},
                "boot_time": datetime.fromtimestamp(psutil.boot_time(), timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"),
            },
            {"library": "psutil"},
        )

    def _process(self, payload: Mapping[str, Any], _observed_at: datetime) -> tuple[dict[str, Any], dict[str, Any]]:
        operation = payload.get("operation", "current")
        if operation == "current":
            process = psutil.Process()
            return ({"process": self._process_row(process)}, {"library": "psutil", "bounded": True})
        if operation != "list":
            raise ObservationFailure("unsupported_operation", "process operation must be current or list")
        limit = payload.get("limit", self._max_processes)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= self._max_processes:
            raise ObservationFailure("invalid_limit", "process limit exceeds the configured bound")
        rows: list[dict[str, Any]] = []
        skipped = 0
        scanned = 0
        scan_limit = max(limit * 4, limit)
        for process in psutil.process_iter():
            if scanned >= scan_limit:
                break
            scanned += 1
            if len(rows) >= limit:
                break
            try:
                rows.append(self._process_row(process))
            except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess, OSError):
                skipped += 1
        rows.sort(key=lambda item: item["pid"])
        return ({"processes": rows, "skipped": skipped}, {"library": "psutil", "limit": limit, "scan_limit": scan_limit, "scanned": scanned})

    @staticmethod
    def _process_row(process: psutil.Process) -> dict[str, Any]:
        with process.oneshot():
            memory = process.memory_info()
            cpu = process.cpu_times()
            try:
                cwd = process.cwd()
            except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
                cwd = None
            return {
                "pid": process.pid,
                "name": process.name(),
                "status": process.status(),
                "cwd": cwd,
                "memory_rss": memory.rss,
                "memory_vms": memory.vms,
                "cpu_user_seconds": cpu.user,
                "cpu_system_seconds": cpu.system,
                "create_time": datetime.fromtimestamp(process.create_time(), timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"),
            }

    def _workspace(self, payload: Mapping[str, Any], _observed_at: datetime) -> tuple[dict[str, Any], dict[str, Any]]:
        root_id, root = self._select_root(self._workspace_roots, payload)
        operation = payload.get("operation", "list")
        path = root.child(payload.get("path", ""))
        if operation == "stat":
            return ({"root_id": root_id, "path": self._relative(root, path), "metadata": self._metadata(path)}, {"bounded": True})
        if operation == "read":
            return (self._read_file(root_id, root, path, payload), {"max_bytes": self._max_bytes, "symlinks_followed": False})
        if operation == "list":
            if not path.is_dir():
                raise ObservationFailure("not_a_directory", "list path must be a directory")
            entries = self._directory_entries(root, path, self._max_entries)
            return ({"root_id": root_id, "path": self._relative(root, path), "entries": entries}, {"max_entries": self._max_entries, "symlinks_followed": False})
        if operation == "tree":
            return (self._tree(root_id, root, path, payload), {"max_depth": self._max_depth, "max_entries": self._max_entries, "symlinks_followed": False})
        raise ObservationFailure("unsupported_operation", "workspace operation must be list, stat, read, or tree")

    @staticmethod
    def _relative(root: _BoundRoot, path: Path) -> str:
        relative = path.relative_to(root.path).as_posix()
        return "." if not relative else relative

    def _metadata(self, path: Path) -> dict[str, Any]:
        try:
            metadata = path.stat(follow_symlinks=False)
        except OSError as exc:
            raise ObservationFailure("metadata_unavailable", "path metadata cannot be read") from exc
        return {
            "type": "directory" if stat.S_ISDIR(metadata.st_mode) else "file" if stat.S_ISREG(metadata.st_mode) else "other",
            "size": metadata.st_size,
            "modified_ns": metadata.st_mtime_ns,
            "mode": stat.S_IMODE(metadata.st_mode),
        }

    def _directory_entries(self, root: _BoundRoot, directory: Path, limit: int) -> list[dict[str, Any]]:
        directory_fd: int | None = None
        try:
            scanned = []
            root.check()
            before = directory.stat(follow_symlinks=False)
            if not stat.S_ISDIR(before.st_mode) or _is_redirect(directory):
                raise ObservationFailure("redirect_blocked", "symlinks and reparse points are not followed")
            if getattr(os, "O_DIRECTORY", 0):
                directory_fd = root.open_relative(directory, directory=True)
            scan_target: int | str = directory_fd if directory_fd is not None else directory
            with os.scandir(scan_target) as iterator:
                for entry in iterator:
                    if len(scanned) >= limit:
                        raise ObservationFailure(
                            "entry_limit_exceeded",
                            "directory exceeds the configured entry limit",
                            evidence={"limit": limit},
                        )
                    scanned.append(entry)
            after = directory.stat(follow_symlinks=False)
            if (before.st_dev, before.st_ino, before.st_mode) != (after.st_dev, after.st_ino, after.st_mode):
                raise ObservationFailure("path_changed", "directory changed during validation")
            root.check()
            scanned.sort(key=lambda item: item.name)
        except ObservationFailure:
            raise
        except OSError as exc:
            raise ObservationFailure("directory_unavailable", "directory cannot be listed") from exc
        finally:
            if directory_fd is not None:
                try:
                    os.close(directory_fd)
                except OSError:
                    pass
        rows = []
        for entry in scanned:
            child = directory / entry.name
            redirected = entry.is_symlink() or _is_redirect(child)
            rows.append({
                "path": self._relative(root, child),
                "redirected": redirected,
                "metadata": {"type": "redirect"} if redirected else self._metadata(child),
            })
        return rows

    def _read_file(self, root_id: str, root: _BoundRoot, path: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
        try:
            expected = path.stat(follow_symlinks=False)
        except OSError as exc:
            raise ObservationFailure("not_a_file", "read path must be a regular file") from exc
        if not stat.S_ISREG(expected.st_mode):
            raise ObservationFailure("not_a_file", "read path must be a regular file")
        requested = payload.get("max_bytes", self._max_bytes)
        if isinstance(requested, bool) or not isinstance(requested, int) or not 1 <= requested <= self._max_bytes:
            raise ObservationFailure("invalid_max_bytes", "requested max_bytes exceeds the configured bound")
        try:
            descriptor = root.open_relative(path) if root._fd is not None else os.open(
                path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            )
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino, opened.st_mode) != (expected.st_dev, expected.st_ino, expected.st_mode):
                os.close(descriptor)
                raise ObservationFailure("path_changed", "file changed during validation")
            with os.fdopen(descriptor, "rb") as stream:
                raw = stream.read(requested + 1)
            root.check()
        except ObservationFailure:
            raise
        except OSError as exc:
            raise ObservationFailure("read_failed", "file content cannot be read") from exc
        truncated = len(raw) > requested
        raw = raw[:requested]
        encoding = "utf-8"
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            encoding = "base64"
            content = base64.b64encode(raw).decode("ascii")
        return {"root_id": root_id, "path": self._relative(root, path), "content": content, "encoding": encoding, "bytes_read": len(raw), "truncated": truncated}

    def _tree(self, root_id: str, root: _BoundRoot, start: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not start.is_dir():
            raise ObservationFailure("not_a_directory", "tree path must be a directory")
        depth_limit = payload.get("max_depth", self._max_depth)
        if isinstance(depth_limit, bool) or not isinstance(depth_limit, int) or not 0 <= depth_limit <= self._max_depth:
            raise ObservationFailure("invalid_max_depth", "requested max_depth exceeds the configured bound")
        rows: list[dict[str, Any]] = []
        pending = [(start, 0)]
        while pending:
            directory, depth = pending.pop()
            for row in self._directory_entries(root, directory, self._max_entries):
                if len(rows) >= self._max_entries:
                    raise ObservationFailure("entry_limit_exceeded", "tree exceeds the configured entry limit", evidence={"limit": self._max_entries})
                row["depth"] = depth
                rows.append(row)
                if row["metadata"]["type"] == "directory" and depth < depth_limit:
                    pending.append((root.child(row["path"]), depth + 1))
        rows.sort(key=lambda item: item["path"])
        return {"root_id": root_id, "path": self._relative(root, start), "entries": rows}

    def _git(self, payload: Mapping[str, Any], _observed_at: datetime) -> tuple[dict[str, Any], dict[str, Any]]:
        root_id, root = self._select_root(self._git_roots, payload)
        try:
            repo = Repo(root.path)
        except Exception as exc:
            raise ObservationFailure("not_a_repository", "bound root is not a Git repository") from exc
        try:
            worktree = Path(os.path.abspath(repo.path))
            if worktree != root.path:
                raise ObservationFailure("repository_root_mismatch", "discovered repository does not match the bound root")
            head = repo.head().decode("ascii")
            head_ref = repo.refs.read_ref(b"HEAD")
            branch = None
            detached = True
            if head_ref and head_ref.startswith(b"ref: refs/heads/"):
                branch = head_ref[len(b"ref: refs/heads/") :].decode("utf-8", "surrogateescape")
                detached = False
            # Dulwich status can walk arbitrarily large worktrees. Run it behind
            # the same wall-time budget so a hostile tree cannot hold the read
            # gateway indefinitely.
            executor = ThreadPoolExecutor(max_workers=1)
            future = executor.submit(porcelain.status, repo, untracked_files="normal")
            try:
                status_result = future.result(timeout=max(0.001, self._http_timeout))
            except FutureTimeout as exc:
                future.cancel()
                raise ObservationFailure("git_timeout", "Git observation exceeded its time limit") from exc
            finally:
                executor.shutdown(wait=False, cancel_futures=True)
            staged_groups = {
                str(change): sorted(_path_text(path).replace("\\", "/") for path in paths)
                for change, paths in status_result.staged.items()
            }
            staged = sorted({path for paths in staged_groups.values() for path in paths})
            unstaged = sorted(_path_text(path).replace("\\", "/") for path in status_result.unstaged)
            unstaged_deleted = {
                path for path in unstaged if not os.path.lexists(root.path / Path(path))
            }
            deleted = sorted(unstaged_deleted | set(staged_groups.get("delete", [])))
            modified = sorted(set(unstaged) - unstaged_deleted)
            untracked = sorted(_path_text(path).replace("\\", "/") for path in status_result.untracked)
            if len(set(staged) | set(unstaged) | set(untracked)) > self._max_entries:
                raise ObservationFailure(
                    "entry_limit_exceeded",
                    "Git status exceeds the configured entry limit",
                    evidence={"limit": self._max_entries},
                )
        except ObservationFailure:
            raise
        except Exception as exc:
            raise ObservationFailure("git_observation_failed", "repository state cannot be read") from exc
        finally:
            repo.close()
        return (
            {"root_id": root_id, "repo_root": str(root.path), "head_sha": head, "branch": branch, "detached": detached, "staged": staged, "staged_changes": staged_groups, "modified": modified, "deleted": deleted, "untracked": untracked},
            {"library": "dulwich", "git_cli_invoked": False, "symlinks_followed": False},
        )

    def _validate_url(self, raw: object) -> tuple[str, tuple[str, str, int | None]]:
        if not isinstance(raw, str) or not raw:
            raise ObservationFailure("invalid_url", "url must be non-empty")
        parsed = urlsplit(raw)
        if parsed.username or parsed.password or not parsed.hostname:
            raise ObservationFailure("invalid_url", "url credentials are not supported")
        scheme = parsed.scheme.lower()
        host = parsed.hostname.lower()
        if scheme not in self._http_allowed_schemes or host not in self._http_allowed_hosts:
            raise ObservationFailure("url_not_allowed", "url is outside the configured HTTP boundary", evidence={"scheme": scheme, "host": host})
        return raw, (scheme, host, parsed.port)

    def _http(self, payload: Mapping[str, Any], _observed_at: datetime) -> tuple[dict[str, Any], dict[str, Any]]:
        method = payload.get("method", "GET")
        if not isinstance(method, str) or method.upper() not in {"GET", "HEAD"}:
            raise ObservationFailure("method_not_allowed", "observation HTTP supports only GET and HEAD")
        method = method.upper()
        url, origin = self._validate_url(payload.get("url"))
        headers = payload.get("headers", {})
        if not isinstance(headers, Mapping) or len(headers) > 32 or any(not isinstance(key, str) or not isinstance(value, str) for key, value in headers.items()):
            raise ObservationFailure("invalid_headers", "headers must be a bounded string mapping")
        if sum(len(key) + len(value) for key, value in headers.items()) > 16_384:
            raise ObservationFailure("invalid_headers", "headers exceed the configured size limit")
        follow = payload.get("follow_redirects", False)
        if not isinstance(follow, bool):
            raise ObservationFailure("invalid_redirect_policy", "follow_redirects must be boolean")
        if follow and self._http_redirect_policy != "same-origin":
            raise ObservationFailure("redirect_blocked", "redirect following is not enabled")
        timeout = httpx.Timeout(self._http_timeout)
        limits = httpx.Limits(max_connections=4, max_keepalive_connections=2)
        safe_headers = {"content-type", "content-length", "etag", "last-modified", "cache-control", "location"}
        redirects = 0
        started = time.monotonic()
        deadline = started + self._http_timeout
        with httpx.Client(timeout=timeout, limits=limits, transport=self._http_transport, follow_redirects=False) as client:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ObservationFailure("http_timeout", "HTTP observation exceeded its total time limit")
                request_timeout = httpx.Timeout(remaining)
                try:
                    with client.stream(method, url, headers=dict(headers), timeout=request_timeout) as response:
                        if response.is_redirect and follow:
                            if redirects >= 3:
                                raise ObservationFailure("redirect_limit_exceeded", "HTTP redirect limit exceeded")
                            location = response.headers.get("location")
                            if not location:
                                raise ObservationFailure("invalid_redirect", "redirect response has no location")
                            next_url = urljoin(url, location)
                            next_url, next_origin = self._validate_url(next_url)
                            if next_origin != origin:
                                raise ObservationFailure("redirect_blocked", "redirect left the original origin")
                            url = next_url
                            redirects += 1
                            continue
                        raw = b""
                        if method != "HEAD":
                            chunks = []
                            size = 0
                            iterator = iter(response.iter_bytes())
                            while True:
                                remaining = deadline - time.monotonic()
                                if remaining <= 0:
                                    raise ObservationFailure("http_timeout", "HTTP observation exceeded its total time limit")
                                executor = ThreadPoolExecutor(max_workers=1)
                                future = executor.submit(next, iterator, None)
                                try:
                                    chunk = future.result(timeout=remaining)
                                except FutureTimeout as exc:
                                    future.cancel()
                                    raise ObservationFailure("http_timeout", "HTTP observation exceeded its total time limit") from exc
                                finally:
                                    executor.shutdown(wait=False, cancel_futures=True)
                                if chunk is None:
                                    break
                                size += len(chunk)
                                if size > self._max_bytes:
                                    raise ObservationFailure("response_too_large", "HTTP response exceeds the configured byte limit", evidence={"max_bytes": self._max_bytes})
                                chunks.append(chunk)
                            raw = b"".join(chunks)
                        if time.monotonic() >= deadline:
                            raise ObservationFailure("http_timeout", "HTTP observation exceeded its total time limit")
                        response_headers = {key.lower(): value for key, value in response.headers.items() if key.lower() in safe_headers}
                        final_url = str(response.url)
                        status_code = response.status_code
                    break
                except httpx.HTTPError as exc:
                    raise ObservationFailure("http_error", type(exc).__name__) from exc
        try:
            body = raw.decode("utf-8")
            body_encoding = "utf-8"
        except UnicodeDecodeError:
            body = base64.b64encode(raw).decode("ascii")
            body_encoding = "base64"
        return (
            {"method": method, "url": final_url, "status_code": status_code, "headers": response_headers, "body": body, "body_encoding": body_encoding, "bytes_read": len(raw), "redirects_followed": redirects},
            {"library": "httpx", "timeout_seconds": self._http_timeout, "max_bytes": self._max_bytes, "redirect_policy": self._http_redirect_policy, "elapsed_ms": round((time.monotonic() - started) * 1000, 3)},
        )


def register_observation_provider(
    provider: RuntimeObservationProvider, inventory: CapabilityInventory
) -> tuple[str, ...]:
    """Register the bundle in the existing inventory; no parallel registry."""
    manifest = provider.manifest()
    rows = manifest["capabilities"]
    capability_ids = [row["capability_id"] for row in rows]
    collisions = [item for item in capability_ids if inventory.get(item) is not None]
    if collisions:
        raise ValueError(f"observation capability already registered: {collisions[0]}")
    for capability_id in capability_ids:
        inventory.register(
            CapabilityBinding(
                capability_id=capability_id,
                kind=CapabilityKind.SERVICE,
                provider=provider.provider_id,
                candidate_id=f"{provider.provider_id}:{capability_id}",
                access_mode=CapabilityAccessMode.LOCAL,
                state=CapabilityLifecycleState.ACTIVE,
                version=provider.version,
                side_effects=SideEffectLevel.READ_ONLY,
                entry_point={"provider": provider.provider_id, "capability_id": capability_id},
                evidence_refs=["manifest:runtime-observation"],
            )
        )
    return tuple(capability_ids)
