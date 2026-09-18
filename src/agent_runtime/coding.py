"""Bounded self-maintenance coding primitives for Runtime-hosted agents."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Mapping

CAP_CODE_INSPECT = "code.inspect"
CAP_CODE_READ = "code.read"
CAP_CODE_EDIT = "code.edit"
CAP_CODE_CREATE = "code.create"
CAP_CODE_TEST = "code.test"
CAP_CODE_DIFF = "code.diff"
CAP_CODE_BRANCH = "code.branch"
CAP_CODE_COMMIT = "code.commit"

_DEFAULT_SUFFIXES = frozenset({
    ".py", ".pyi", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx",
    ".html", ".css", ".json", ".yaml", ".yml", ".toml", ".md",
    ".txt", ".sh", ".service", ".conf", ".ini",
})
_DEFAULT_NAMES = frozenset({"Dockerfile", "Makefile", "Procfile"})
_DENIED_PARTS = frozenset({".git", ".ssh", "secrets", ".venv", "venv", "node_modules"})
_DENIED_NAMES = frozenset({".env", ".env.local", "credentials.json", "auth.json"})
_DENIED_SUFFIXES = frozenset({".pem", ".key", ".p12", ".pfx"})
_SENSITIVE_ENV = re.compile(r"(SECRET|TOKEN|PASSWORD|CREDENTIAL|API[_-]?KEY|PRIVATE[_-]?KEY)", re.I)
@dataclass(frozen=True)
class CodingPolicy:
    repo_root: Path
    test_profiles: Mapping[str, tuple[tuple[str, ...], ...]] = field(default_factory=dict)
    allowed_suffixes: frozenset[str] = _DEFAULT_SUFFIXES
    allowed_names: frozenset[str] = _DEFAULT_NAMES
    protected_branches: frozenset[str] = frozenset({"main", "master"})
    branch_prefix: str = "agent/"
    max_file_bytes: int = 256_000
    max_diff_bytes: int = 256_000
    max_test_seconds: float = 180.0
    require_tests_before_commit: bool = True
    allow_commit: bool = True

    def __post_init__(self) -> None:
        root = Path(self.repo_root).resolve()
        if not root.is_dir():
            raise ValueError("coding repo_root must be an existing directory")
        if not self.branch_prefix or any(ch.isspace() for ch in self.branch_prefix):
            raise ValueError("branch_prefix must be a non-empty identifier")
        if self.max_file_bytes <= 0 or self.max_diff_bytes <= 0:
            raise ValueError("coding size limits must be positive")
        if self.max_test_seconds <= 0:
            raise ValueError("max_test_seconds must be positive")
        object.__setattr__(self, "repo_root", root)


class BoundedCodingProvider:
    """Deterministic code-maintenance operations; never pushes or deploys."""

    provider_id = "agent-runtime-bounded-coding"

    def __init__(self, policy: CodingPolicy) -> None:
        self.policy = policy
        self.root = policy.repo_root
        self._assert_git_root()
        self._last_tested_fingerprint: str | None = None
    def manifest(self) -> dict[str, Any]:
        def row(capability_id: str, description: str, level: str, permission: str) -> dict[str, Any]:
            return {
                "capability_id": capability_id,
                "description": description,
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
                "side_effect_level": level,
                "required_permissions": [permission],
            }

        return {
            "provider": self.provider_id,
            "version": "1.0",
            "description": "Bounded local source maintenance with no push, merge, deploy, or secret access.",
            "capabilities": [
                row(CAP_CODE_INSPECT, "Inspect branch and working-tree state.", "READ_ONLY", "workspace.code.read"),
                row(CAP_CODE_READ, "Read an allowlisted source file.", "READ_ONLY", "workspace.code.read"),
                row(CAP_CODE_DIFF, "Read the current local diff.", "READ_ONLY", "workspace.code.read"),
                row(CAP_CODE_EDIT, "Apply one exact bounded text replacement.", "LOCAL_STATE", "workspace.code.write"),
                row(CAP_CODE_CREATE, "Create one allowlisted source file.", "LOCAL_STATE", "workspace.code.write"),
                row(CAP_CODE_TEST, "Run a deployment-defined test profile.", "LOCAL_STATE", "workspace.code.test"),
                row(CAP_CODE_BRANCH, "Create an isolated local task branch.", "LOCAL_STATE", "workspace.code.branch"),
                row(CAP_CODE_COMMIT, "Create a tested local commit; never push.", "LOCAL_STATE", "workspace.code.commit"),
            ],
        }

    def invoke(self, capability_id: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        args = dict(payload or {})
        handlers = {
            CAP_CODE_INSPECT: self.inspect,
            CAP_CODE_READ: self.read,
            CAP_CODE_DIFF: self.diff,
            CAP_CODE_EDIT: self.edit,
            CAP_CODE_CREATE: self.create,
            CAP_CODE_TEST: self.test,
            CAP_CODE_BRANCH: self.branch,
            CAP_CODE_COMMIT: self.commit,
        }
        try:
            handler = handlers[capability_id]
        except KeyError as exc:
            raise ValueError(f"unsupported coding capability: {capability_id}") from exc
        return handler(args)
    def inspect(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if payload:
            raise ValueError("code.inspect takes no arguments")
        branch = self._git("branch", "--show-current").stdout.strip()
        changed = self._changed_paths()
        return {
            "ok": True,
            "branch": branch or None,
            "protected_branch": branch in self.policy.protected_branches,
            "clean": not changed,
            "changed_paths": changed,
            "test_profiles": sorted(self.policy.test_profiles),
            "push_available": False,
            "deploy_available": False,
        }

    def read(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        target = self._target(str(payload.get("path") or ""), must_exist=True)
        raw = target.read_bytes()
        if len(raw) > self.policy.max_file_bytes:
            raise ValueError("source file exceeds coding read limit")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("coding provider reads UTF-8 text only") from exc
        start = int(payload.get("start_line") or 1)
        limit = int(payload.get("max_lines") or 240)
        if start < 1 or limit < 1 or limit > 500:
            raise ValueError("invalid line window")
        lines = text.splitlines()
        selected = lines[start - 1 : start - 1 + limit]
        return {
            "ok": True,
            "path": self._relative(target),
            "start_line": start,
            "end_line": start + max(0, len(selected) - 1),
            "truncated": start - 1 + len(selected) < len(lines),
            "text": "\n".join(selected),
        }
    def edit(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        target = self._target(str(payload.get("path") or ""), must_exist=True)
        old = payload.get("old_text")
        new = payload.get("new_text")
        if not isinstance(old, str) or not old:
            raise ValueError("code.edit requires non-empty old_text")
        if not isinstance(new, str):
            raise ValueError("code.edit requires string new_text")
        expected = int(payload.get("expected_replacements") or 1)
        if expected < 1 or expected > 50:
            raise ValueError("expected_replacements must be 1..50")
        text = self._read_editable(target)
        count = text.count(old)
        if count != expected:
            raise ValueError(f"exact replacement count mismatch: expected={expected} actual={count}")
        updated = text.replace(old, new)
        self._atomic_write(target, updated)
        return {
            "ok": True,
            "path": self._relative(target),
            "replacements": count,
            "bytes": len(updated.encode("utf-8")),
        }

    def create(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        target = self._target(str(payload.get("path") or ""), must_exist=False)
        if target.exists():
            raise ValueError("code.create refuses to overwrite an existing file")
        if not target.parent.is_dir():
            raise ValueError("code.create requires an existing parent directory")
        content = payload.get("content")
        if not isinstance(content, str):
            raise ValueError("code.create requires string content")
        if len(content.encode("utf-8")) > self.policy.max_file_bytes:
            raise ValueError("created file exceeds coding write limit")
        self._atomic_write(target, content, mode=0o644)
        return {"ok": True, "path": self._relative(target), "bytes": len(content.encode("utf-8"))}
    def diff(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if payload:
            raise ValueError("code.diff takes no arguments")
        changed = self._changed_paths()
        tracked = set(self._tracked_changed_paths())
        visible_tracked = [item for item in changed if item in tracked]
        text = ""
        if visible_tracked:
            text = self._git(
                "diff", "--no-ext-diff", "--unified=3", "--", *visible_tracked
            ).stdout
        untracked = [item for item in changed if item not in tracked]
        if untracked:
            text += "\nUntracked files:\n" + "\n".join(sorted(untracked)) + "\n"
        raw = text.encode("utf-8")
        truncated = len(raw) > self.policy.max_diff_bytes
        if truncated:
            text = raw[: self.policy.max_diff_bytes].decode("utf-8", errors="ignore")
        return {
            "ok": True,
            "diff": text,
            "truncated": truncated,
            "changed_paths": self._changed_paths(),
        }

    def branch(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if self._tracked_changed_paths():
            raise ValueError("code.branch requires no tracked working-tree changes")
        task = str(payload.get("task_id") or payload.get("name") or "").strip()
        slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", task).strip("-._").lower()[:72]
        if not slug:
            raise ValueError("code.branch requires a bounded task_id")
        branch = self.policy.branch_prefix + slug
        if branch in self.policy.protected_branches:
            raise ValueError("refusing protected branch")
        existing = self._git("branch", "--list", branch).stdout.strip()
        if existing:
            raise ValueError("task branch already exists")
        self._git("switch", "-c", branch)
        self._last_tested_fingerprint = None
        return {"ok": True, "branch": branch}
    def test(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        profile = str(payload.get("profile") or "").strip()
        commands = self.policy.test_profiles.get(profile)
        if not profile or commands is None:
            raise ValueError("code.test requires a deployment-defined profile")
        results: list[dict[str, Any]] = []
        env = self._sanitized_env()
        for command in commands:
            if not command or any(not isinstance(part, str) or not part for part in command):
                raise ValueError("invalid configured test command")
            try:
                completed = subprocess.run(
                    tuple(command),
                    cwd=self.root,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=self.policy.max_test_seconds,
                    check=False,
                )
                row = {
                    "command": list(command),
                    "exit_code": int(completed.returncode),
                    "stdout": self._bounded_output(completed.stdout),
                    "stderr": self._bounded_output(completed.stderr),
                }
            except subprocess.TimeoutExpired as exc:
                row = {
                    "command": list(command),
                    "exit_code": None,
                    "timed_out": True,
                    "stdout": self._bounded_output(self._text(exc.stdout)),
                    "stderr": self._bounded_output(self._text(exc.stderr)),
                }
            results.append(row)
            if row.get("exit_code") != 0:
                self._last_tested_fingerprint = None
                return {"ok": False, "profile": profile, "results": results}
        self._last_tested_fingerprint = self._fingerprint()
        return {"ok": True, "profile": profile, "results": results}
    def commit(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not self.policy.allow_commit:
            raise PermissionError("local commits are disabled by coding policy")
        branch = self._git("branch", "--show-current").stdout.strip()
        if not branch or branch in self.policy.protected_branches:
            raise PermissionError("code.commit requires a non-protected task branch")
        changed = self._changed_paths()
        if not changed:
            raise ValueError("code.commit requires local changes")
        for path in changed:
            self._target(path, must_exist=None)
        if (
            self.policy.require_tests_before_commit
            and self._last_tested_fingerprint != self._fingerprint()
        ):
            raise PermissionError("code.commit requires passing tests for the current working tree")
        message = str(payload.get("message") or "").strip()
        if not message or len(message) > 160 or "\n" in message:
            raise ValueError("commit message must be a single line of 1..160 characters")
        self._git("diff", "--check", "--", *changed)
        self._git("add", "--", *changed)
        self._git("commit", "-m", message)
        sha = self._git("rev-parse", "HEAD").stdout.strip()
        self._last_tested_fingerprint = None
        return {"ok": True, "branch": branch, "commit": sha, "changed_paths": changed, "pushed": False}
    def _assert_git_root(self) -> None:
        root = self._git("rev-parse", "--show-toplevel").stdout.strip()
        if Path(root).resolve() != self.root:
            raise ValueError("coding repo_root must be the git worktree root")

    def _git(self, *args: str) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ("git", *args),
            cwd=self.root,
            env=self._sanitized_env(),
            capture_output=True,
            text=True,
            timeout=max(5.0, min(self.policy.max_test_seconds, 60.0)),
            check=False,
        )
        if result.returncode != 0:
            detail = self._bounded_output(result.stderr or result.stdout, 4000).strip()
            raise RuntimeError(f"git command failed: {detail or result.returncode}")
        return result

    def _target(self, relative: str, *, must_exist: bool | None) -> Path:
        value = str(relative or "").replace("\\", "/")
        path = Path(value)
        if not value or path.is_absolute() or ".." in path.parts:
            raise ValueError("coding path must be repository-relative")
        if any(part in _DENIED_PARTS for part in path.parts):
            raise PermissionError("coding path is protected")
        if path.name in _DENIED_NAMES or path.suffix.lower() in _DENIED_SUFFIXES:
            raise PermissionError("coding path is protected")
        if path.name not in self.policy.allowed_names and path.suffix.lower() not in self.policy.allowed_suffixes:
            raise PermissionError("coding path type is not allowlisted")
        target = (self.root / path).resolve(strict=False)
        try:
            target.relative_to(self.root)
        except ValueError as exc:
            raise PermissionError("coding path escapes repository root") from exc
        if target.is_symlink():
            raise PermissionError("coding provider does not follow symlink files")
        if must_exist is True and not target.is_file():
            raise FileNotFoundError(relative)
        if must_exist is False and target.exists():
            raise FileExistsError(relative)
        return target
    def _read_editable(self, target: Path) -> str:
        raw = target.read_bytes()
        if len(raw) > self.policy.max_file_bytes:
            raise ValueError("source file exceeds coding write limit")
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("coding provider edits UTF-8 text only") from exc

    def _atomic_write(self, target: Path, text: str, *, mode: int | None = None) -> None:
        raw = text.encode("utf-8")
        if len(raw) > self.policy.max_file_bytes:
            raise ValueError("source file exceeds coding write limit")
        existing_mode = mode
        if target.exists():
            existing_mode = target.stat().st_mode & 0o777
        if existing_mode is None:
            existing_mode = 0o644
        tmp = target.with_name("." + target.name + ".agent-runtime.tmp")
        descriptor = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, existing_mode)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
            os.chmod(tmp, existing_mode)
            os.replace(tmp, target)
        except Exception:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise

    def _tracked_changed_paths(self) -> list[str]:
        names: set[str] = set()
        for args in (("diff", "--name-only"), ("diff", "--cached", "--name-only")):
            names.update(
                line.strip()
                for line in self._git(*args).stdout.splitlines()
                if line.strip()
            )
        return sorted(names)

    def _changed_paths(self) -> list[str]:
        names = set(self._tracked_changed_paths())
        names.update(
            line.strip()
            for line in self._git(
                "ls-files", "--others", "--exclude-standard"
            ).stdout.splitlines()
            if line.strip()
        )
        return sorted(item for item in names if self._path_is_allowed(item))

    def _path_is_allowed(self, relative: str) -> bool:
        try:
            self._target(relative, must_exist=None)
        except (ValueError, PermissionError):
            return False
        return True

    def _fingerprint(self) -> str:
        digest = hashlib.sha256()
        for relative in self._changed_paths():
            digest.update(relative.encode("utf-8"))
            target = self._target(relative, must_exist=None)
            if not target.exists():
                digest.update(b"<deleted>")
                continue
            if target.stat().st_size > self.policy.max_file_bytes:
                raise ValueError("changed source file exceeds coding fingerprint limit")
            digest.update(target.read_bytes())
        return digest.hexdigest()

    def _relative(self, target: Path) -> str:
        return target.relative_to(self.root).as_posix()

    @staticmethod
    def _text(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)

    @staticmethod
    def _bounded_output(value: str, limit: int = 20_000) -> str:
        text = str(value or "")
        return text if len(text) <= limit else text[:limit] + "\n...[truncated]"

    @staticmethod
    def _sanitized_env() -> dict[str, str]:
        return {
            key: value
            for key, value in os.environ.items()
            if not _SENSITIVE_ENV.search(key)
        }


__all__ = [
    "BoundedCodingProvider",
    "CodingPolicy",
    "CAP_CODE_INSPECT", "CAP_CODE_READ", "CAP_CODE_EDIT", "CAP_CODE_CREATE",
    "CAP_CODE_TEST", "CAP_CODE_DIFF", "CAP_CODE_BRANCH", "CAP_CODE_COMMIT",
]
