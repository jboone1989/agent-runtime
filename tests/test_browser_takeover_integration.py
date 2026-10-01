from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_runtime.capabilities.inventory import CapabilityInventory
from agent_runtime.capabilities.model import SideEffectLevel
from agent_runtime.integrations import (
    BROWSER_PROFILE_START,
    BROWSER_PROFILE_STATUS,
    BROWSER_PROFILE_STOP,
    BROWSER_TAKEOVER_RELEASE,
    BROWSER_TAKEOVER_START,
    BROWSER_TAKEOVER_STATUS,
    BrowserTakeoverPolicy,
    PersistentBrowserTakeoverProvider,
    register_browser_takeover_provider,
)


class _FakeProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.alive = True

    def poll(self):
        return None if self.alive else 0


class _FakeHost:
    def __init__(self) -> None:
        self.next_pid = 1000
        self.processes: dict[int, _FakeProcess] = {}
        self.argv: dict[int, list[str]] = {}
        self.open_ports: dict[int, int] = {}
        self.cdp_ports: dict[int, int] = {}
        self.terminated: list[int] = []
        self.kwargs: dict[int, dict] = {}
        self.commands: list[list[str]] = []

    def popen(self, argv, **kwargs):
        pid = self.next_pid
        self.next_pid += 1
        process = _FakeProcess(pid)
        args = [str(x) for x in argv]
        self.processes[pid] = process
        self.argv[pid] = args
        self.kwargs[pid] = dict(kwargs)

        if "-rfbport" in args:
            port = int(args[args.index("-rfbport") + 1])
            self.open_ports[port] = pid
        for arg in args:
            if arg.startswith("--remote-debugging-port="):
                port = int(arg.split("=", 1)[1])
                self.cdp_ports[port] = pid
                self.open_ports[port] = pid
        if len(args) >= 2 and args[-2].startswith("127.0.0.1:"):
            listen = args[-2]
            port = int(listen.rsplit(":", 1)[1])
            self.open_ports[port] = pid
        return process

    def run(self, argv, **kwargs):
        self.commands.append([str(x) for x in argv])
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def port_probe(self, port: int) -> bool:
        pid = self.open_ports.get(int(port))
        return bool(pid and self.process_alive(pid))

    def cdp_probe(self, port: int) -> bool:
        pid = self.cdp_ports.get(int(port))
        return bool(pid and self.process_alive(pid))

    def process_alive(self, pid: int) -> bool:
        process = self.processes.get(int(pid))
        return bool(process and process.alive)

    def terminate(self, pid: int) -> None:
        process = self.processes.get(int(pid))
        if process is None or not process.alive:
            return
        process.alive = False
        self.terminated.append(int(pid))
        for mapping in (self.open_ports, self.cdp_ports):
            for port, owner in list(mapping.items()):
                if owner == pid:
                    del mapping[port]


def _executable(path: Path) -> Path:
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(0o700)
    return path


def _provider(
    tmp_path: Path,
    *,
    profile_dirs: dict[str, Path] | None = None,
):
    host = _FakeHost()
    chrome = _executable(tmp_path / "chrome")
    desktop = _executable(tmp_path / "Xtigervnc")
    websockify = _executable(tmp_path / "websockify")
    wm = _executable(tmp_path / "openbox")
    xauth = _executable(tmp_path / "xauth")
    novnc = tmp_path / "novnc"
    novnc.mkdir()
    (novnc / "vnc.html").write_text("<html></html>", encoding="utf-8")

    provider = PersistentBrowserTakeoverProvider(
        state_root=tmp_path / "state",
        policy=BrowserTakeoverPolicy(
            display_start=70,
            display_end=70,
            cdp_port_start=9600,
            cdp_port_end=9600,
            websocket_port_start=6080,
            websocket_port_end=6080,
            startup_timeout_seconds=1,
        ),
        chrome_binary=chrome,
        desktop_binary=desktop,
        websockify_binary=websockify,
        novnc_root=novnc,
        window_manager_binary=wm,
        xauth_binary=xauth,
        profile_dirs=profile_dirs,
        popen_factory=host.popen,
        command_runner=host.run,
        port_probe=host.port_probe,
        cdp_probe=host.cdp_probe,
        process_alive=host.process_alive,
        terminate=host.terminate,
        sleep=lambda _: None,
    )
    return provider, host


def test_takeover_manifest_and_permissions(tmp_path: Path) -> None:
    provider, _ = _provider(tmp_path)
    inventory = CapabilityInventory()

    registered = register_browser_takeover_provider(provider, inventory)

    assert set(registered) == {
        BROWSER_PROFILE_START,
        BROWSER_PROFILE_STATUS,
        BROWSER_PROFILE_STOP,
        BROWSER_TAKEOVER_START,
        BROWSER_TAKEOVER_STATUS,
        BROWSER_TAKEOVER_RELEASE,
    }
    assert (
        inventory.get(BROWSER_PROFILE_STATUS).side_effects
        is SideEffectLevel.READ_ONLY
    )
    assert inventory.get(BROWSER_PROFILE_START).entry_point[
        "required_permissions"
    ] == ["browser.profile.manage"]
    assert inventory.get(BROWSER_TAKEOVER_START).entry_point[
        "required_permissions"
    ] == ["browser.takeover"]


def test_profile_start_persists_real_browser_binding_and_close_keeps_it_alive(
    tmp_path: Path,
) -> None:
    provider, host = _provider(tmp_path)

    started = provider.invoke(
        BROWSER_PROFILE_START,
        {"profile_id": "chatgpt", "url": "https://chatgpt.com/"},
    )

    assert started["running"] is True
    assert started["display"] == 70
    assert started["cdp_url"] == "http://127.0.0.1:9600"
    state_path = (
        tmp_path
        / "state"
        / "browser"
        / "profiles"
        / "chatgpt"
        / "runtime.json"
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["chrome_pid"] == started["chrome_pid"]
    assert state["desktop_pid"] == started["desktop_pid"]
    assert Path(started["profile_dir"]).name == "chrome-profile"
    authority = (
        tmp_path
        / "state"
        / "browser"
        / "profiles"
        / "chatgpt"
        / "Xauthority"
    )
    assert authority.is_file()
    assert host.commands[0][0].endswith("/xauth")
    assert host.commands[0][1:3] == ["-f", str(authority)]
    desktop_argv = host.argv[started["desktop_pid"]]
    assert desktop_argv[desktop_argv.index("-auth") + 1] == str(authority)
    chrome_env = host.kwargs[started["chrome_pid"]]["env"]
    assert chrome_env["DISPLAY"] == ":70"
    assert chrome_env["XAUTHORITY"] == str(authority)

    provider.close()

    assert host.process_alive(started["chrome_pid"])
    assert host.process_alive(started["desktop_pid"])
    assert host.terminated == []



def test_existing_logged_in_profile_can_be_adopted_without_copy_or_delete(
    tmp_path: Path,
) -> None:
    legacy_profile = tmp_path / "legacy-ferro-chatgpt-profile"
    legacy_profile.mkdir()
    sentinel = legacy_profile / "Login Data"
    sentinel.write_text("existing-session", encoding="utf-8")
    provider, host = _provider(
        tmp_path,
        profile_dirs={"chatgpt": legacy_profile},
    )

    started = provider.invoke(
        BROWSER_PROFILE_START,
        {"profile_id": "chatgpt", "url": "https://chatgpt.com/"},
    )

    assert Path(started["profile_dir"]) == legacy_profile
    assert sentinel.read_text(encoding="utf-8") == "existing-session"
    chrome_argv = host.argv[started["chrome_pid"]]
    assert f"--user-data-dir={legacy_profile}" in chrome_argv

    provider.invoke(
        BROWSER_PROFILE_STOP,
        {"profile_id": "chatgpt"},
    )

    assert legacy_profile.is_dir()
    assert sentinel.read_text(encoding="utf-8") == "existing-session"


def test_takeover_uses_novnc_and_release_keeps_browser_running(
    tmp_path: Path,
) -> None:
    provider, host = _provider(tmp_path)

    takeover = provider.invoke(
        BROWSER_TAKEOVER_START,
        {"profile_id": "youtube", "url": "https://www.youtube.com/"},
    )

    assert takeover["active"] is True
    assert takeover["browser_running"] is True
    assert takeover["transport"] == "NOVNC"
    assert takeover["url"].startswith("http://127.0.0.1:6080/vnc.html")
    assert takeover["websocket_url"] == "ws://127.0.0.1:6080/websockify"
    assert takeover["cdp_url"] == "http://127.0.0.1:9600"

    chrome_pid = provider.profile_status("youtube").chrome_pid
    desktop_pid = provider.profile_status("youtube").desktop_pid
    websockify_pid = takeover["websockify_pid"]

    released = provider.invoke(
        BROWSER_TAKEOVER_RELEASE,
        {"profile_id": "youtube"},
    )

    assert released["active"] is False
    assert released["browser_running"] is True
    assert websockify_pid in host.terminated
    assert host.process_alive(chrome_pid)
    assert host.process_alive(desktop_pid)


def test_profile_stop_is_the_explicit_full_shutdown(tmp_path: Path) -> None:
    provider, host = _provider(tmp_path)
    takeover = provider.invoke(
        BROWSER_TAKEOVER_START,
        {"profile_id": "okx", "url": "https://www.okx.com/"},
    )
    status = provider.profile_status("okx")
    pids = {
        status.chrome_pid,
        status.desktop_pid,
        status.window_manager_pid,
        takeover["websockify_pid"],
    }

    stopped = provider.invoke(
        BROWSER_PROFILE_STOP,
        {"profile_id": "okx"},
    )

    assert stopped["running"] is False
    assert {pid for pid in pids if pid is not None}.issubset(
        set(host.terminated)
    )
    assert not (
        tmp_path
        / "state"
        / "browser"
        / "profiles"
        / "okx"
        / "runtime.json"
    ).exists()


def test_readiness_reports_mature_takeover_dependencies(tmp_path: Path) -> None:
    provider, _ = _provider(tmp_path)

    readiness = provider.readiness()

    assert readiness["status"] == "READY"
    assert readiness["missing"] == []
    assert readiness["chrome"].endswith("/chrome")
    assert readiness["desktop"].endswith("/Xtigervnc")
    assert readiness["websockify"].endswith("/websockify")
    assert readiness["novnc_root"].endswith("/novnc")
    assert readiness["xauth"].endswith("/xauth")


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"profile_id": "../bad"}, "invalid browser profile_id"),
        (
            {"profile_id": "safe", "url": "file:///etc/passwd"},
            "browser url must use http/https",
        ),
    ],
)
def test_takeover_rejects_invalid_profile_or_url(
    tmp_path: Path,
    payload: dict,
    message: str,
) -> None:
    provider, _ = _provider(tmp_path)

    with pytest.raises(ValueError, match=message):
        provider.invoke(BROWSER_PROFILE_START, payload)
