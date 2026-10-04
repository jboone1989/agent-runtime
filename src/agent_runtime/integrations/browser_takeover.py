"""Persistent browser profiles and human takeover for Agent Runtime.

Automation remains delegated to Browser Harness over CDP. This module owns only
persistent browser/profile process lifecycle and a mature human-takeover path:
TigerVNC provides the desktop and noVNC/websockify exposes it to an authenticated
host application. It does not reimplement browser screenshots, mouse, keyboard,
DOM interaction, or page understanding.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from contextlib import contextmanager
import glob
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import socket
import subprocess
import threading
import time
from typing import Any
from urllib.parse import quote, urlsplit
import urllib.request


@contextmanager
def _host_browser_lock(state_root: Path):
    path = state_root / "browser" / "shared.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            handle.write(b"0")
            handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield

from ..capabilities.inventory import CapabilityInventory
from ..capabilities.model import (
    CapabilityAccessMode,
    CapabilityBinding,
    CapabilityKind,
    CapabilityLifecycleState,
    SideEffectLevel,
)


BROWSER_PROFILE_START = "browser.profile.start"
BROWSER_PROFILE_STATUS = "browser.profile.status"
BROWSER_PROFILE_STOP = "browser.profile.stop"
BROWSER_TAKEOVER_START = "browser.takeover.start"
BROWSER_TAKEOVER_STATUS = "browser.takeover.status"
BROWSER_TAKEOVER_RELEASE = "browser.takeover.release"

_CAPABILITIES = (
    BROWSER_PROFILE_START,
    BROWSER_PROFILE_STATUS,
    BROWSER_PROFILE_STOP,
    BROWSER_TAKEOVER_START,
    BROWSER_TAKEOVER_STATUS,
    BROWSER_TAKEOVER_RELEASE,
)


class BrowserTakeoverUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class BrowserTakeoverPolicy:
    width: int = 1280
    height: int = 900
    display_start: int = 70
    display_end: int = 99
    cdp_port_start: int = 9600
    cdp_port_end: int = 9699
    websocket_port_start: int = 6080
    websocket_port_end: int = 6179
    startup_timeout_seconds: float = 20.0

    def __post_init__(self) -> None:
        if not 640 <= int(self.width) <= 3840:
            raise ValueError("width must be in [640, 3840]")
        if not 480 <= int(self.height) <= 2160:
            raise ValueError("height must be in [480, 2160]")
        for low, high, name in (
            (self.display_start, self.display_end, "display"),
            (self.cdp_port_start, self.cdp_port_end, "cdp port"),
            (self.websocket_port_start, self.websocket_port_end, "websocket port"),
        ):
            if not 1 <= int(low) <= int(high) <= 65535:
                raise ValueError(f"invalid {name} range")
        if not 1 <= float(self.startup_timeout_seconds) <= 120:
            raise ValueError("startup_timeout_seconds must be in [1, 120]")


@dataclass(frozen=True)
class BrowserProfileStatus:
    profile_id: str
    running: bool
    profile_dir: str
    display: int | None
    cdp_url: str | None
    chrome_pid: int | None
    desktop_pid: int | None
    window_manager_pid: int | None


@dataclass(frozen=True)
class BrowserTakeoverStatus:
    profile_id: str
    active: bool
    browser_running: bool
    url: str | None
    websocket_url: str | None
    cdp_url: str | None
    websockify_pid: int | None
    transport: str = "NOVNC"


class PersistentBrowserTakeoverProvider:
    """Persistent Chromium + TigerVNC/noVNC lifecycle.

    The provider binds only loopback endpoints. A host application such as
    LivingRuntime Remote is expected to authenticate and proxy the noVNC URL.
    Calling close() does not terminate the browser so login state survives
    Agent Runtime restarts. Explicit browser.profile.stop does terminate it.
    """

    provider_name = "native-browser-takeover"
    contract_version = "runtime-browser-takeover:v1"

    def __init__(
        self,
        *,
        state_root: Path,
        policy: BrowserTakeoverPolicy | None = None,
        chrome_binary: str | Path | None = None,
        desktop_binary: str | Path | None = None,
        websockify_binary: str | Path | None = None,
        novnc_root: str | Path | None = None,
        window_manager_binary: str | Path | None = None,
        xauth_binary: str | Path | None = None,
        profile_dirs: Mapping[str, str | Path] | None = None,
        popen_factory: Callable[..., Any] = subprocess.Popen,
        command_runner: Callable[..., Any] = subprocess.run,
        port_probe: Callable[[int], bool] | None = None,
        cdp_probe: Callable[[int], bool] | None = None,
        process_alive: Callable[[int], bool] | None = None,
        terminate: Callable[[int], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.state_root = Path(state_root)
        self.policy = policy or BrowserTakeoverPolicy()
        self._explicit_chrome = str(chrome_binary) if chrome_binary else ""
        self._explicit_desktop = str(desktop_binary) if desktop_binary else ""
        self._explicit_websockify = str(websockify_binary) if websockify_binary else ""
        self._explicit_novnc_root = str(novnc_root) if novnc_root else ""
        self._explicit_window_manager = (
            str(window_manager_binary) if window_manager_binary else ""
        )
        self._explicit_xauth = str(xauth_binary) if xauth_binary else ""
        self._profile_dirs = {
            _profile_id(key): Path(value).expanduser()
            for key, value in (profile_dirs or {}).items()
        }
        self._popen = popen_factory
        self._run = command_runner
        self._port_probe = port_probe or _port_open
        self._cdp_probe = cdp_probe or _cdp_ready
        self._process_alive = process_alive or _pid_alive
        self._terminate = terminate or _terminate_pid
        self._sleep = sleep
        self._lock = threading.RLock()
        self._closed = False

    def manifest(self) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        for capability_id in _CAPABILITIES:
            read_only = capability_id in {
                BROWSER_PROFILE_STATUS,
                BROWSER_TAKEOVER_STATUS,
            }
            takeover = capability_id in {
                BROWSER_TAKEOVER_START,
                BROWSER_TAKEOVER_RELEASE,
            }
            rows.append(
                {
                    "capability_id": capability_id,
                    "provider": self.provider_name,
                    "version": self.contract_version,
                    "side_effect_level": "READ_ONLY" if read_only else "LOCAL_STATE",
                    "required_permissions": (
                        []
                        if read_only
                        else ["browser.takeover"]
                        if takeover
                        else ["browser.profile.manage"]
                    ),
                }
            )
        return {
            "provider": self.provider_name,
            "version": self.contract_version,
            "capabilities": rows,
        }

    def readiness(self) -> dict[str, Any]:
        resolved: dict[str, str] = {}
        missing: list[str] = []
        for label, resolver in (
            ("chrome", self._chrome_binary),
            ("desktop", self._desktop_binary),
            ("websockify", self._websockify_binary),
            ("novnc_root", self._novnc_root),
            ("xauth", self._xauth_binary),
        ):
            try:
                resolved[label] = str(resolver())
            except BrowserTakeoverUnavailable:
                missing.append(label)
        wm = self._window_manager_binary(required=False)
        if wm is not None:
            resolved["window_manager"] = str(wm)
        return {
            "provider": self.provider_name,
            "status": "READY" if not missing else "NOT_INSTALLED",
            "missing": missing,
            **resolved,
        }

    def invoke(
        self,
        capability_id: str,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self._closed:
            raise RuntimeError("browser takeover provider is closed")
        request = dict(payload or {})
        profile_id = _profile_id(request.get("profile_id", "default"))
        if capability_id == BROWSER_PROFILE_START:
            result = self.start_profile(
                profile_id,
                url=_url(request.get("url", "about:blank"), allow_about_blank=True),
            )
        elif capability_id == BROWSER_PROFILE_STATUS:
            result = self.profile_status(profile_id)
        elif capability_id == BROWSER_PROFILE_STOP:
            result = self.stop_profile(profile_id)
        elif capability_id == BROWSER_TAKEOVER_START:
            result = self.start_takeover(
                profile_id,
                url=_url(request.get("url", "about:blank"), allow_about_blank=True),
            )
        elif capability_id == BROWSER_TAKEOVER_STATUS:
            result = self.takeover_status(profile_id)
        elif capability_id == BROWSER_TAKEOVER_RELEASE:
            result = self.release_takeover(profile_id)
        else:
            raise ValueError(f"unsupported browser takeover capability: {capability_id}")
        return {"ok": True, "capability_id": capability_id, **asdict(result)}

    def close(self) -> None:
        self._closed = True

    def start_profile(
        self,
        profile_id: str,
        *,
        url: str = "about:blank",
    ) -> BrowserProfileStatus:
        profile_id = _profile_id(profile_id)
        url = _url(url, allow_about_blank=True)
        with self._lock, _host_browser_lock(self.state_root):
            requested = profile_id
            profile_id = self._shared_owner(requested)
            current = self.profile_status(profile_id)
            if current.running:
                if url != "about:blank":
                    self._open_url(current.cdp_url, url)
                if requested != profile_id:
                    self._write_state(requested, self._read_state(profile_id))
                return self.profile_status(requested)

            folder = self._profile_folder(profile_id)
            profile_dir = self._profile_dir(profile_id)
            folder.mkdir(parents=True, exist_ok=True)
            profile_dir.mkdir(parents=True, exist_ok=True)
            os.chmod(folder, 0o700)
            os.chmod(profile_dir, 0o700)

            # Chrome can die while the persistent X desktop remains healthy.
            # Reuse that desktop instead of trying to allocate the same display
            # again; the authenticated Chrome profile stays on disk and only
            # the browser process needs to be recreated.
            previous = self._read_state(profile_id)
            previous_display = _int_or_none(previous.get("display"))
            previous_vnc_port = _int_or_none(previous.get("vnc_port"))
            previous_desktop_pid = _int_or_none(previous.get("desktop_pid"))
            previous_authority = previous.get("xauthority_path")
            reuse_desktop = bool(
                previous_display is not None
                and previous_vnc_port is not None
                and previous_desktop_pid is not None
                and isinstance(previous_authority, str)
                and Path(previous_authority).is_file()
                and self._process_alive(previous_desktop_pid)
                and self._port_probe(previous_vnc_port)
            )
            if reuse_desktop:
                display = previous_display
                vnc_port = previous_vnc_port
                display_name = f":{display}"
                authority_path = Path(previous_authority)
                desktop_pid = previous_desktop_pid
                previous_cdp_port = _int_or_none(previous.get("cdp_port"))
                cdp_port = (
                    previous_cdp_port
                    if previous_cdp_port is not None
                    and not self._port_probe(previous_cdp_port)
                    else self._allocate_port(
                        self.policy.cdp_port_start,
                        self.policy.cdp_port_end,
                    )
                )
                env = dict(os.environ)
                env["DISPLAY"] = display_name
                env["XAUTHORITY"] = str(authority_path)
                wm_process = None
                window_manager_pid = _int_or_none(
                    previous.get("window_manager_pid")
                )
                if not (
                    window_manager_pid is not None
                    and self._process_alive(window_manager_pid)
                ):
                    wm = self._window_manager_binary(required=False)
                    if wm is not None:
                        wm_process = self._popen(
                            [str(wm)],
                            env=env,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            start_new_session=True,
                        )
                        window_manager_pid = int(wm_process.pid)
                chrome = self._popen(
                    [
                        str(self._chrome_binary()),
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                        "--no-first-run",
                        "--no-default-browser-check",
                        "--disable-session-crashed-bubble",
                        "--remote-debugging-address=127.0.0.1",
                        f"--remote-debugging-port={cdp_port}",
                        f"--user-data-dir={profile_dir}",
                        f"--window-size={self.policy.width},{self.policy.height}",
                        "--start-maximized",
                        url,
                    ],
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                try:
                    self._wait_for_cdp(cdp_port, process=chrome)
                except Exception:
                    self._terminate(int(getattr(chrome, "pid", 0) or 0))
                    if wm_process is not None:
                        self._terminate(int(wm_process.pid))
                    raise
                state = {
                    **previous,
                    "schema_version": 1,
                    "profile_id": profile_id,
                    "display": display,
                    "vnc_port": vnc_port,
                    "cdp_port": cdp_port,
                    "desktop_pid": desktop_pid,
                    "window_manager_pid": window_manager_pid,
                    "chrome_pid": int(chrome.pid),
                    "xauthority_path": str(authority_path),
                }
                self._write_state(profile_id, state)
                self._write_shared_owner(profile_id)
                if requested != profile_id:
                    self._write_state(requested, state)
                return self.profile_status(requested)

            display = self._allocate_display()
            vnc_port = 5900 + display
            cdp_port = self._allocate_port(
                self.policy.cdp_port_start,
                self.policy.cdp_port_end,
            )
            display_name = f":{display}"
            authority_path = folder / "Xauthority"
            authority_path.touch(mode=0o600, exist_ok=True)
            os.chmod(authority_path, 0o600)
            cookie = secrets.token_hex(16)
            xauth = self._run(
                [
                    str(self._xauth_binary()),
                    "-f",
                    str(authority_path),
                    "add",
                    display_name,
                    ".",
                    cookie,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if int(xauth.returncode) != 0:
                detail = str(xauth.stderr or xauth.stdout or "").strip()
                raise BrowserTakeoverUnavailable(
                    "could not create Xauthority"
                    + (f": {detail[-500:]}" if detail else "")
                )
            desktop = self._popen(
                [
                    str(self._desktop_binary()),
                    display_name,
                    "-auth",
                    str(authority_path),
                    "-localhost",
                    "-SecurityTypes",
                    "None",
                    "-geometry",
                    f"{self.policy.width}x{self.policy.height}",
                    "-depth",
                    "24",
                    "-rfbport",
                    str(vnc_port),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            wm_process = None
            chrome = None
            try:
                self._wait_for_port(vnc_port, process=desktop, label="desktop")
                env = dict(os.environ)
                env["DISPLAY"] = display_name
                env["XAUTHORITY"] = str(authority_path)
                wm = self._window_manager_binary(required=False)
                if wm is not None:
                    wm_process = self._popen(
                        [str(wm)],
                        env=env,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                chrome = self._popen(
                    [
                        str(self._chrome_binary()),
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                        "--no-first-run",
                        "--no-default-browser-check",
                        "--disable-session-crashed-bubble",
                        "--remote-debugging-address=127.0.0.1",
                        f"--remote-debugging-port={cdp_port}",
                        f"--user-data-dir={profile_dir}",
                        f"--window-size={self.policy.width},{self.policy.height}",
                        "--start-maximized",
                        url,
                    ],
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                self._wait_for_cdp(cdp_port, process=chrome)
            except Exception:
                if chrome is not None:
                    self._terminate(int(getattr(chrome, "pid", 0) or 0))
                if wm_process is not None:
                    self._terminate(int(getattr(wm_process, "pid", 0) or 0))
                self._terminate(int(getattr(desktop, "pid", 0) or 0))
                raise

            state = {
                "schema_version": 1,
                "profile_id": profile_id,
                "display": display,
                "vnc_port": vnc_port,
                "cdp_port": cdp_port,
                "desktop_pid": int(desktop.pid),
                "window_manager_pid": (
                    int(wm_process.pid) if wm_process is not None else None
                ),
                "chrome_pid": int(chrome.pid),
                "xauthority_path": str(authority_path),
                "websocket_port": None,
                "websockify_pid": None,
            }
            self._write_state(profile_id, state)
            self._write_shared_owner(profile_id)
            if requested != profile_id:
                self._write_state(requested, state)
            return self.profile_status(requested)

    def profile_status(self, profile_id: str) -> BrowserProfileStatus:
        profile_id = _profile_id(profile_id)
        body = self._read_state(profile_id)
        cdp_port = _int_or_none(body.get("cdp_port"))
        chrome_pid = _int_or_none(body.get("chrome_pid"))
        desktop_pid = _int_or_none(body.get("desktop_pid"))
        running = bool(
            cdp_port
            and chrome_pid
            and desktop_pid
            and self._cdp_probe(cdp_port)
            and self._process_alive(chrome_pid)
            and self._process_alive(desktop_pid)
        )
        return BrowserProfileStatus(
            profile_id=profile_id,
            running=running,
            profile_dir=str(self._profile_dir(self._shared_owner(profile_id))),
            display=_int_or_none(body.get("display")),
            cdp_url=f"http://127.0.0.1:{cdp_port}" if cdp_port else None,
            chrome_pid=chrome_pid,
            desktop_pid=desktop_pid,
            window_manager_pid=_int_or_none(body.get("window_manager_pid")),
        )

    def stop_profile(self, profile_id: str) -> BrowserProfileStatus:
        profile_id = _profile_id(profile_id)
        with self._lock, _host_browser_lock(self.state_root):
            body = self._read_state(profile_id)
            for field in (
                "websockify_pid",
                "chrome_pid",
                "window_manager_pid",
                "desktop_pid",
            ):
                pid = _int_or_none(body.get(field))
                if pid is not None:
                    self._terminate(pid)
            for path in (self.state_root / "browser" / "profiles").glob("*/runtime.json"):
                if json.loads(path.read_text()).get("chrome_pid") == body.get("chrome_pid"):
                    path.unlink()
            return self.profile_status(profile_id)

    def start_takeover(
        self,
        profile_id: str,
        *,
        url: str = "about:blank",
    ) -> BrowserTakeoverStatus:
        profile_id = _profile_id(profile_id)
        url = _url(url, allow_about_blank=True)
        with self._lock:
            profile = self.start_profile(profile_id, url=url)
            body = self._read_state(profile_id)
            existing_pid = _int_or_none(body.get("websockify_pid"))
            existing_port = _int_or_none(body.get("websocket_port"))
            if (
                existing_pid is not None
                and existing_port is not None
                and self._process_alive(existing_pid)
                and self._port_probe(existing_port)
            ):
                return self.takeover_status(profile_id)

            websocket_port = self._allocate_port(
                self.policy.websocket_port_start,
                self.policy.websocket_port_end,
            )
            vnc_port = _required_int(body.get("vnc_port"), "vnc_port")
            websockify = self._popen(
                [
                    str(self._websockify_binary()),
                    "--web",
                    str(self._novnc_root()),
                    f"127.0.0.1:{websocket_port}",
                    f"127.0.0.1:{vnc_port}",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            try:
                self._wait_for_port(
                    websocket_port,
                    process=websockify,
                    label="noVNC websocket",
                )
            except Exception:
                self._terminate(int(getattr(websockify, "pid", 0) or 0))
                raise

            body["websocket_port"] = websocket_port
            body["websockify_pid"] = int(websockify.pid)
            self._write_state(self._shared_owner(profile_id), body)
            return self.takeover_status(profile_id)

    def takeover_status(self, profile_id: str) -> BrowserTakeoverStatus:
        profile_id = _profile_id(profile_id)
        profile = self.profile_status(profile_id)
        body = self._read_state(profile_id)
        pid = _int_or_none(body.get("websockify_pid"))
        port = _int_or_none(body.get("websocket_port"))
        active = bool(
            profile.running
            and pid
            and port
            and self._process_alive(pid)
            and self._port_probe(port)
        )
        return BrowserTakeoverStatus(
            profile_id=profile_id,
            active=active,
            browser_running=profile.running,
            url=_novnc_url(port) if active and port else None,
            websocket_url=(
                f"ws://127.0.0.1:{port}/websockify"
                if active and port
                else None
            ),
            cdp_url=profile.cdp_url,
            websockify_pid=pid if active else None,
        )

    def release_takeover(self, profile_id: str) -> BrowserTakeoverStatus:
        profile_id = _profile_id(profile_id)
        with self._lock:
            body = self._read_state(profile_id)
            pid = _int_or_none(body.get("websockify_pid"))
            if pid is not None:
                self._terminate(pid)
            body["websocket_port"] = None
            body["websockify_pid"] = None
            if body:
                self._write_state(self._shared_owner(profile_id), body)
            return self.takeover_status(profile_id)

    def _profile_folder(self, profile_id: str) -> Path:
        return self.state_root / "browser" / "profiles" / profile_id

    def _profile_dir(self, profile_id: str) -> Path:
        shared = self.state_root / "browser" / "shared.json"
        if shared.exists():
            body = json.loads(shared.read_text())
            if body.get("profile_id") == profile_id and body.get("profile_dir"):
                return Path(body["profile_dir"])
        return self._profile_dirs.get(
            profile_id,
            self._profile_folder(profile_id) / "chrome-profile",
        )

    def _runtime_path(self, profile_id: str) -> Path:
        return self._profile_folder(profile_id) / "runtime.json"

    def _read_state(self, profile_id: str) -> dict[str, Any]:
        path = self._runtime_path(self._shared_owner(profile_id))
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except json.JSONDecodeError as exc:
            raise BrowserTakeoverUnavailable(
                f"invalid browser runtime state: {path}"
            ) from exc
        if not isinstance(value, dict):
            raise BrowserTakeoverUnavailable(
                f"invalid browser runtime state: {path}"
            )
        return value

    def _shared_owner(self, requested: str) -> str:
        shared = self.state_root / "browser" / "shared.json"
        if shared.exists():
            return _profile_id(json.loads(shared.read_text())["profile_id"])
        # Adopt an existing browser without starting another. Multiple legacy
        # processes require the explicit authenticated migration first.
        live = {}
        for path in (self.state_root / "browser" / "profiles").glob("*/runtime.json"):
            state = json.loads(path.read_text())
            pid, port = state.get("chrome_pid"), state.get("cdp_port")
            if pid and port and self._process_alive(pid) and self._cdp_probe(port):
                live[pid] = state["profile_id"]
        if len(live) > 1:
            raise BrowserTakeoverUnavailable("multiple legacy browsers are running; migrate their login state first")
        if live:
            owner = next(iter(live.values()))
            self._write_shared_owner(owner)
            return owner
        return requested

    def _write_shared_owner(self, profile_id: str) -> None:
        path = self.state_root / "browser" / "shared.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"profile_id": profile_id, "profile_dir": str(self._profile_dir(profile_id))}) + "\n")
        os.replace(tmp, path)

    def _write_state(self, profile_id: str, value: Mapping[str, Any]) -> None:
        folder = self._profile_folder(profile_id)
        folder.mkdir(parents=True, exist_ok=True)
        os.chmod(folder, 0o700)
        path = self._runtime_path(profile_id)
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(dict(value), handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
        os.replace(tmp, path)
        os.chmod(path, 0o600)
        # Logical site aliases must follow owner PID/port changes after a real
        # Chrome restart; consumers also read these files directly.
        if value.get("profile_id") == profile_id:
            for alias in (self.state_root / "browser" / "profiles").glob("*/runtime.json"):
                if alias == path:
                    continue
                if json.loads(alias.read_text()).get("profile_id") == profile_id:
                    alias_tmp = alias.with_suffix(".tmp")
                    with os.fdopen(os.open(alias_tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as handle:
                        json.dump(dict(value), handle, sort_keys=True, separators=(",", ":"))
                        handle.write("\n")
                    os.replace(alias_tmp, alias)

    def _allocate_display(self) -> int:
        for display in range(self.policy.display_start, self.policy.display_end + 1):
            if Path(f"/tmp/.X11-unix/X{display}").exists():
                continue
            if not self._port_probe(5900 + display):
                return display
        raise BrowserTakeoverUnavailable("no free browser desktop display")

    def _allocate_port(self, start: int, end: int) -> int:
        for port in range(start, end + 1):
            if not self._port_probe(port):
                return port
        raise BrowserTakeoverUnavailable("no free browser runtime port")

    def _wait_for_port(self, port: int, *, process: Any, label: str) -> None:
        deadline = time.monotonic() + self.policy.startup_timeout_seconds
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise BrowserTakeoverUnavailable(f"{label} exited during startup")
            if self._port_probe(port):
                return
            self._sleep(0.1)
        raise BrowserTakeoverUnavailable(f"{label} did not become ready")

    def _wait_for_cdp(self, port: int, *, process: Any) -> None:
        deadline = time.monotonic() + self.policy.startup_timeout_seconds
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise BrowserTakeoverUnavailable("Chromium exited during startup")
            if self._cdp_probe(port):
                return
            self._sleep(0.1)
        raise BrowserTakeoverUnavailable("Chromium CDP did not become ready")

    def _open_url(self, cdp_url: str | None, url: str) -> None:
        if not cdp_url:
            raise BrowserTakeoverUnavailable("browser CDP endpoint unavailable")
        try:
            with urllib.request.urlopen(cdp_url + "/json/list", timeout=3) as response:
                targets = json.load(response)
            if any(row.get("type") == "page" and row.get("url") == url for row in targets):
                return
        except OSError as exc:
            raise BrowserTakeoverUnavailable("could not inspect persistent browser tabs") from exc
        request = urllib.request.Request(
            cdp_url + "/json/new?" + quote(url, safe=""),
            method="PUT",
        )
        try:
            with urllib.request.urlopen(request, timeout=3):
                return
        except OSError as exc:
            raise BrowserTakeoverUnavailable(
                "could not open URL in persistent browser"
            ) from exc

    def _chrome_binary(self) -> Path:
        value = (
            self._explicit_chrome
            or os.environ.get("AGENT_RUNTIME_CHROME_BINARY", "").strip()
        )
        if value:
            return _executable(value, "Chromium")
        candidates = sorted(
            glob.glob(
                str(Path.home() / ".cache/ms-playwright/**/chrome-linux64/chrome"),
                recursive=True,
            )
        )
        for candidate in reversed(candidates):
            if os.access(candidate, os.X_OK):
                return Path(candidate)
        for command in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
            found = shutil.which(command)
            if found:
                return Path(found)
        raise BrowserTakeoverUnavailable("Chromium binary is unavailable")

    def _desktop_binary(self) -> Path:
        value = (
            self._explicit_desktop
            or os.environ.get("AGENT_RUNTIME_XTIGERVNC", "").strip()
        )
        if value:
            return _executable(value, "Xtigervnc")
        for candidate in (
            Path.home() / ".local/lib/agent-runtime-browser/bin/Xtigervnc",
            Path("/usr/bin/Xtigervnc"),
        ):
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate
        found = shutil.which("Xtigervnc")
        if found:
            return Path(found)
        raise BrowserTakeoverUnavailable("Xtigervnc is unavailable")

    def _websockify_binary(self) -> Path:
        value = (
            self._explicit_websockify
            or os.environ.get("AGENT_RUNTIME_WEBSOCKIFY", "").strip()
        )
        if value:
            return _executable(value, "websockify")
        for candidate in (
            Path.home() / ".local/lib/agent-runtime-browser/venv/bin/websockify",
            Path.home() / ".local/bin/websockify",
        ):
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate
        found = shutil.which("websockify")
        if found:
            return Path(found)
        raise BrowserTakeoverUnavailable("websockify is unavailable")

    def _novnc_root(self) -> Path:
        value = (
            self._explicit_novnc_root
            or os.environ.get("AGENT_RUNTIME_NOVNC_ROOT", "").strip()
        )
        candidates = [
            Path(value) if value else None,
            Path.home() / ".local/lib/agent-runtime-browser/novnc",
            Path("/usr/share/novnc"),
        ]
        for candidate in candidates:
            if candidate is not None and (candidate / "vnc.html").is_file():
                return candidate
        raise BrowserTakeoverUnavailable("noVNC static client is unavailable")

    def _window_manager_binary(self, *, required: bool) -> Path | None:
        value = (
            self._explicit_window_manager
            or os.environ.get("AGENT_RUNTIME_WINDOW_MANAGER", "").strip()
        )
        if value:
            return _executable(value, "window manager")
        for command in ("openbox", "fluxbox"):
            found = shutil.which(command)
            if found:
                return Path(found)
        candidate = Path.home() / ".local/lib/agent-runtime-browser/bin/openbox"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
        if required:
            raise BrowserTakeoverUnavailable("window manager is unavailable")
        return None

    def _xauth_binary(self) -> Path:
        value = (
            self._explicit_xauth
            or os.environ.get("AGENT_RUNTIME_XAUTH", "").strip()
        )
        if value:
            return _executable(value, "xauth")
        for candidate in (
            Path("/usr/bin/xauth"),
            Path.home() / ".local/bin/xauth",
        ):
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate
        found = shutil.which("xauth")
        if found:
            return Path(found)
        raise BrowserTakeoverUnavailable("xauth is unavailable")


def register_browser_takeover_provider(
    provider: PersistentBrowserTakeoverProvider,
    inventory: CapabilityInventory,
) -> tuple[str, ...]:
    registered: list[str] = []
    for row in provider.manifest()["capabilities"]:
        capability_id = str(row["capability_id"])
        if inventory.get(capability_id) is not None:
            raise ValueError(
                f"browser takeover capability already registered: {capability_id}"
            )
        inventory.register(
            CapabilityBinding(
                capability_id=capability_id,
                kind=CapabilityKind.SERVICE,
                provider=provider.provider_name,
                candidate_id=f"{provider.provider_name}:{capability_id}",
                access_mode=CapabilityAccessMode.LOCAL,
                state=CapabilityLifecycleState.ACTIVE,
                version=provider.contract_version,
                side_effects=SideEffectLevel[str(row["side_effect_level"])],
                entry_point={
                    "adapter": "PersistentBrowserTakeoverProvider",
                    "required_permissions": list(row.get("required_permissions") or ()),
                },
                evidence_refs=["contract:runtime-browser-takeover:v1"],
            )
        )
        registered.append(capability_id)
    return tuple(registered)


def _profile_id(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("profile_id must be a string")
    value = value.strip()
    if (
        not value
        or len(value) > 64
        or not value[0].isalnum()
        or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for ch in value)
    ):
        raise ValueError("invalid browser profile_id")
    return value


def _url(value: Any, *, allow_about_blank: bool = False) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        raise ValueError("browser url must be a bounded non-empty string")
    raw = value.strip()
    if allow_about_blank and raw == "about:blank":
        return raw
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("browser url must use http/https")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("credential-bearing browser URLs are not allowed")
    return raw


def _executable(value: str | Path, label: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_file() or not os.access(path, os.X_OK):
        raise BrowserTakeoverUnavailable(
            f"{label} executable is unavailable: {path}"
        )
    return path


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    return None


def _required_int(value: Any, field: str) -> int:
    parsed = _int_or_none(value)
    if parsed is None:
        raise BrowserTakeoverUnavailable(f"browser runtime missing {field}")
    return parsed


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=0.2):
            return True
    except OSError:
        return False


def _cdp_ready(port: int) -> bool:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{int(port)}/json/version",
            timeout=0.5,
        ) as response:
            body = json.load(response)
        return isinstance(body, dict) and bool(body.get("webSocketDebuggerUrl"))
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def _pid_alive(pid: int) -> bool:
    if not isinstance(pid, int) or pid <= 1:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _terminate_pid(pid: int) -> None:
    if not isinstance(pid, int) or pid <= 1:
        return
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError as exc:
        raise BrowserTakeoverUnavailable(
            f"cannot terminate browser runtime process group: {pid}"
        ) from exc


def _novnc_url(port: int) -> str:
    return (
        f"http://127.0.0.1:{int(port)}/vnc.html"
        "?autoconnect=1&resize=remote&reconnect=1&path=websockify"
    )


__all__ = [
    "BROWSER_PROFILE_START",
    "BROWSER_PROFILE_STATUS",
    "BROWSER_PROFILE_STOP",
    "BROWSER_TAKEOVER_START",
    "BROWSER_TAKEOVER_STATUS",
    "BROWSER_TAKEOVER_RELEASE",
    "BrowserProfileStatus",
    "BrowserTakeoverPolicy",
    "BrowserTakeoverStatus",
    "BrowserTakeoverUnavailable",
    "PersistentBrowserTakeoverProvider",
    "register_browser_takeover_provider",
]
