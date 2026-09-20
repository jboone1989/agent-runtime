from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import PurePosixPath
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

NAME = "LivingRuntime Remote"
VERSION = "0.1.0"
MAX_OUTPUT_BYTES = 262144
DEFAULT_TIMEOUT = 30

server = FastMCP(NAME)

_ALLOWED_EXECUTABLES = {
    "cargo", "df", "du", "find", "go", "head", "make", "node",
    "npm", "npx", "pnpm", "ps", "pytest", "python", "python3",
    "ruff", "sed", "ss", "tail", "uv",
}
_BLOCKED_INLINE = {
    "python": {"-c"},
    "python3": {"-c"},
    "node": {"-e", "--eval"},
}
_BLOCKED_GIT = {"credential", "daemon", "shell"}

_REMOTE_AGENT = r"""
import hashlib, json, os, pathlib, signal, subprocess, sys
req = json.load(sys.stdin)
roots = [pathlib.Path(x).expanduser().resolve(strict=True) for x in req["roots"]]
def inside(value, exists=True):
    p = pathlib.Path(value).expanduser()
    if exists or p.exists() or p.is_symlink():
        p = p.resolve(strict=True)
    else:
        p = p.parent.resolve(strict=True) / p.name
    for root in roots:
        try:
            p.relative_to(root)
            return p
        except ValueError:
            pass
    raise PermissionError("path outside configured roots: " + str(p))
def digest(p):
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()
op = req["op"]
if op == "exec":
    cwd = inside(req["cwd"])
    if not cwd.is_dir():
        raise ValueError("cwd must be a directory")
    p = subprocess.run(
        req["argv"], cwd=str(cwd), stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=req["timeout"], check=False,
    )
    limit = req["max_output"]
    print(json.dumps({
        "returncode": p.returncode,
        "stdout": p.stdout[:limit].decode("utf-8", errors="replace"),
        "stderr": p.stderr[:limit].decode("utf-8", errors="replace"),
        "cwd": str(cwd),
    }))
elif op == "read_file":
    p = inside(req["path"])
    if not p.is_file():
        raise ValueError("path is not a file")
    with p.open("rb") as fh:
        fh.seek(req["offset"])
        data = fh.read(req["max_bytes"])
    print(json.dumps({
        "path": str(p), "offset": req["offset"], "bytes_read": len(data),
        "content": data.decode("utf-8", errors="replace"), "sha256": digest(p),
    }))
elif op == "write_file":
    p = inside(req["path"], exists=False)
    p.parent.mkdir(parents=True, exist_ok=True)
    expected = req.get("expected_sha256")
    if expected is not None:
        if not p.exists():
            raise FileNotFoundError("expected_sha256 supplied but file does not exist")
        if digest(p) != expected:
            raise RuntimeError("sha256 mismatch")
    with p.open("a" if req["mode"] == "append" else "w", encoding="utf-8") as fh:
        fh.write(req["content"])
    print(json.dumps({"path": str(p), "mode": req["mode"], "bytes": p.stat().st_size, "sha256": digest(p)}))
elif op == "list_dir":
    p = inside(req["path"])
    if not p.is_dir():
        raise ValueError("path is not a directory")
    children = sorted(p.iterdir(), key=lambda x: x.name)
    rows = []
    for child in children[:req["max_entries"]]:
        rows.append({
            "name": child.name,
            "type": "dir" if child.is_dir() else "file" if child.is_file() else "other",
            "size": child.stat().st_size if child.is_file() else None,
        })
    print(json.dumps({"path": str(p), "entries": rows, "truncated": len(children) > len(rows)}))
elif op == "process":
    uid = os.getuid()
    if req["action"] == "list":
        rows = []
        for proc in sorted(pathlib.Path("/proc").iterdir(), key=lambda p: p.name):
            if not proc.name.isdigit():
                continue
            try:
                if proc.stat().st_uid != uid:
                    continue
                cwd = (proc / "cwd").resolve(strict=True)
                inside(str(cwd))
                command = (proc / "cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
                if req.get("contains") and req["contains"] not in command:
                    continue
                rows.append({"pid": int(proc.name), "cwd": str(cwd), "command": command})
                if len(rows) >= 500:
                    break
            except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
                pass
        print(json.dumps({"processes": rows}))
    elif req["action"] == "terminate":
        pid = int(req.get("pid") or 0)
        if pid <= 1:
            raise ValueError("refusing pid <= 1")
        proc = pathlib.Path("/proc") / str(pid)
        if proc.stat().st_uid != uid:
            raise PermissionError("process owner mismatch")
        cwd = (proc / "cwd").resolve(strict=True)
        inside(str(cwd))
        os.kill(pid, signal.SIGTERM)
        print(json.dumps({"pid": pid, "signal": "SIGTERM", "cwd": str(cwd)}))
    else:
        raise ValueError("action must be list or terminate")
else:
    raise ValueError("unknown operation")
"""


def _config_path() -> str:
    return os.environ.get(
        "LIVINGRUNTIME_REMOTE_CONFIG",
        os.path.join(os.path.expanduser("~"), ".livingruntime", "remote.json"),
    )


def _config() -> dict[str, Any]:
    try:
        with open(_config_path(), "r", encoding="utf-8") as fh:
            value = json.load(fh)
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"LivingRuntime Remote is not configured; run scripts/configure.py first ({_config_path()})"
        ) from exc
    if not isinstance(value, dict):
        raise RuntimeError("remote config must be a JSON object")
    return value


def _host() -> str:
    value = str(_config().get("ssh_host") or "").strip()
    if not value or value.startswith("-") or any(ch.isspace() for ch in value):
        raise RuntimeError("remote config contains an invalid ssh_host")
    return value


def _roots() -> tuple[str, ...]:
    raw = _config().get("roots") or ["/home/ubuntu"]
    if not isinstance(raw, list):
        raise RuntimeError("roots must be a JSON array")
    roots = tuple(str(PurePosixPath(str(x))) for x in raw if str(x).strip())
    if not roots:
        raise RuntimeError("roots must not be empty")
    return roots


def _units() -> set[str]:
    raw = _config().get("systemd_units") or []
    if not isinstance(raw, list):
        raise RuntimeError("systemd_units must be a JSON array")
    return {str(x).strip() for x in raw if str(x).strip()}


def _audit(tool: str, ok: bool, detail: dict[str, Any]) -> None:
    directory = os.path.join(os.path.expanduser("~"), ".livingruntime")
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, "remote-audit.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(
            {"ts": time.time(), "tool": tool, "ok": ok, "detail": detail},
            ensure_ascii=False, sort_keys=True,
        ) + "\n")


def _ssh(remote_argv: list[str], *, stdin: bytes | None = None, timeout: int = DEFAULT_TIMEOUT) -> dict[str, Any]:
    command = shlex.join(remote_argv)
    started = time.monotonic()
    proc = subprocess.run(
        [
            "ssh", "-T",
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=yes",
            "-o", "ConnectTimeout=10",
            "-o", "ServerAliveInterval=15",
            "-o", "ServerAliveCountMax=2",
            _host(), command,
        ],
        input=b"" if stdin is None else stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=min(150, max(1, int(timeout)) + 12),
        check=False,
    )
    def bounded(data: bytes) -> str:
        if len(data) > MAX_OUTPUT_BYTES:
            data = data[:MAX_OUTPUT_BYTES] + b"\n...[truncated]..."
        return data.decode("utf-8", errors="replace")
    return {
        "returncode": proc.returncode,
        "stdout": bounded(proc.stdout),
        "stderr": bounded(proc.stderr),
        "duration_ms": round((time.monotonic() - started) * 1000, 2),
    }


def _remote(op: str, payload: dict[str, Any], timeout: int = DEFAULT_TIMEOUT) -> dict[str, Any]:
    request = {"op": op, "roots": list(_roots()), **payload}
    result = _ssh(
        ["python3", "-c", _REMOTE_AGENT],
        stdin=json.dumps(request, ensure_ascii=False).encode("utf-8"),
        timeout=timeout,
    )
    if result["returncode"] != 0:
        raise RuntimeError(result["stderr"] or result["stdout"] or "remote operation failed")
    return json.loads(result["stdout"])


def _validate_exec(argv: list[str]) -> None:
    if not argv:
        raise ValueError("argv is required")
    executable = os.path.basename(str(argv[0]))
    if executable not in _ALLOWED_EXECUTABLES:
        raise PermissionError(f"executable is not allowlisted: {executable}")
    if any(arg in _BLOCKED_INLINE.get(executable, set()) for arg in argv[1:]):
        raise PermissionError(f"inline code flag is blocked for {executable}")
    if any("\x00" in str(arg) for arg in argv):
        raise ValueError("NUL bytes are not allowed")


def _validate_git(args: list[str]) -> None:
    if not args:
        raise ValueError("git args are required")
    subcommand = next((x for x in args if not str(x).startswith("-")), "")
    if not subcommand:
        raise ValueError("git subcommand is required")
    if subcommand in _BLOCKED_GIT:
        raise PermissionError(f"git subcommand is blocked: {subcommand}")
    blocked_flags = {"-c", "-C", "--git-dir", "--work-tree", "--config-env"}
    if any(
        arg in blocked_flags
        or str(arg).startswith("--git-dir=")
        or str(arg).startswith("--work-tree=")
        for arg in args
    ):
        raise PermissionError("git path/config override flags are blocked")
    joined = " ".join(map(str, args))
    if "credential.helper" in joined or "core.sshCommand" in joined:
        raise PermissionError("credential and ssh command overrides are blocked")


@server.tool(
    annotations=ToolAnnotations(
        title="Connection status",
        readOnlyHint=True,
        openWorldHint=True,
    )
)
def connection_status() -> dict[str, Any]:
    """Verify key-based SSH connectivity and report the configured remote scope."""
    result = _ssh(
        ["python3", "-c", "import getpass,json,os,socket; print(json.dumps({'user':getpass.getuser(),'host':socket.gethostname(),'cwd':os.getcwd()}))"],
        timeout=12,
    )
    ok = result["returncode"] == 0
    _audit("connection_status", ok, {"host_alias": _host()})
    if not ok:
        raise RuntimeError(result["stderr"] or "SSH connection failed")
    return {
        "ok": True,
        "remote": json.loads(result["stdout"].strip()),
        "configured_roots": list(_roots()),
        "configured_systemd_units": sorted(_units()),
        "version": VERSION,
    }


@server.tool(
    annotations=ToolAnnotations(
        title="Execute development command",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    )
)
def exec(argv: list[str], cwd: str | None = None, timeout_seconds: int = DEFAULT_TIMEOUT) -> dict[str, Any]:
    """Run one allowlisted development executable; shell command strings are not accepted."""
    _validate_exec(argv)
    timeout = min(120, max(1, int(timeout_seconds)))
    result = _remote("exec", {
        "argv": argv, "cwd": cwd or _roots()[0],
        "timeout": timeout, "max_output": MAX_OUTPUT_BYTES,
    }, timeout=timeout + 2)
    _audit("exec", True, {"argv": argv, "cwd": result.get("cwd"), "returncode": result.get("returncode")})
    return result


@server.tool(
    annotations=ToolAnnotations(
        title="Read remote file",
        readOnlyHint=True,
        openWorldHint=True,
    )
)
def read_file(path: str, offset: int = 0, max_bytes: int = 131072) -> dict[str, Any]:
    """Read a bounded byte range from a file under a configured remote root."""
    result = _remote("read_file", {
        "path": path, "offset": max(0, int(offset)),
        "max_bytes": min(MAX_OUTPUT_BYTES, max(1, int(max_bytes))),
    })
    _audit("read_file", True, {"path": result["path"], "bytes_read": result["bytes_read"]})
    return result


@server.tool(
    annotations=ToolAnnotations(
        title="Write remote file",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    )
)
def write_file(path: str, content: str, mode: str = "replace", expected_sha256: str | None = None) -> dict[str, Any]:
    """Write UTF-8 text under a configured root, optionally guarded by current SHA-256."""
    if mode not in {"replace", "append"}:
        raise ValueError("mode must be replace or append")
    if len(content.encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise ValueError("content exceeds maximum write size")
    result = _remote("write_file", {
        "path": path, "content": content, "mode": mode,
        "expected_sha256": expected_sha256,
    })
    _audit("write_file", True, {"path": result["path"], "mode": mode, "bytes": result["bytes"]})
    return result


@server.tool(
    annotations=ToolAnnotations(
        title="List remote directory",
        readOnlyHint=True,
        openWorldHint=True,
    )
)
def list_dir(path: str, max_entries: int = 200) -> dict[str, Any]:
    """List one remote directory under a configured root."""
    result = _remote("list_dir", {
        "path": path, "max_entries": min(1000, max(1, int(max_entries))),
    })
    _audit("list_dir", True, {"path": result["path"], "entries": len(result["entries"])})
    return result


@server.tool(
    annotations=ToolAnnotations(
        title="Run remote Git operation",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    )
)
def git(repo_path: str, args: list[str], timeout_seconds: int = DEFAULT_TIMEOUT) -> dict[str, Any]:
    """Run a Git subcommand in a configured remote repository."""
    _validate_git(args)
    timeout = min(120, max(1, int(timeout_seconds)))
    result = _remote("exec", {
        "argv": ["git", *args], "cwd": repo_path,
        "timeout": timeout, "max_output": MAX_OUTPUT_BYTES,
    }, timeout=timeout + 2)
    _audit("git", True, {"repo_path": repo_path, "args": args, "returncode": result.get("returncode")})
    return result


@server.tool(
    annotations=ToolAnnotations(
        title="Inspect or terminate remote process",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    )
)
def process(action: str, pid: int | None = None, contains: str | None = None) -> dict[str, Any]:
    """List scoped same-user processes or SIGTERM one process whose cwd is under a configured root."""
    result = _remote("process", {"action": action, "pid": pid, "contains": contains})
    _audit("process", True, {"action": action, "pid": pid})
    return result


@server.tool(
    annotations=ToolAnnotations(
        title="Control allowlisted remote service",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    )
)
def systemd(action: str, unit: str) -> dict[str, Any]:
    """Inspect or control an explicitly allowlisted remote systemd service."""
    if unit not in _units():
        raise PermissionError("unit is not allowlisted")
    if action not in {"status", "is-active", "start", "stop", "restart"}:
        raise ValueError("unsupported systemd action")
    argv = ["systemctl", action, unit]
    if action in {"start", "stop", "restart"}:
        argv = ["sudo", "-n", *argv]
    result = _ssh(argv, timeout=30)
    _audit("systemd", result["returncode"] == 0, {"action": action, "unit": unit})
    return result


@server.tool(
    annotations=ToolAnnotations(
        title="Read allowlisted remote service logs",
        readOnlyHint=True,
        openWorldHint=True,
    )
)
def logs(unit: str, lines: int = 200, since_minutes: int = 60) -> dict[str, Any]:
    """Read bounded journal logs for an explicitly allowlisted remote systemd service."""
    if unit not in _units():
        raise PermissionError("unit is not allowlisted")
    result = _ssh([
        "journalctl", "--no-pager", "-u", unit,
        "-n", str(min(1000, max(1, int(lines)))),
        "--since", f"{min(10080, max(1, int(since_minutes)))} minutes ago",
    ], timeout=30)
    _audit("logs", result["returncode"] == 0, {"unit": unit, "lines": lines})
    return result


if __name__ == "__main__":
    server.run(transport="stdio")
