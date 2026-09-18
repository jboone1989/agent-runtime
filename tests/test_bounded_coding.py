from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

from agent_runtime.application import ApplicationRuntime
from agent_runtime.coding import (
    BoundedCodingProvider,
    CAP_CODE_BRANCH,
    CAP_CODE_COMMIT,
    CAP_CODE_DIFF,
    CAP_CODE_EDIT,
    CAP_CODE_INSPECT,
    CAP_CODE_READ,
    CAP_CODE_TEST,
    CodingPolicy,
)


PERMISSIONS = frozenset({
    "workspace.code.read",
    "workspace.code.write",
    "workspace.code.test",
    "workspace.code.branch",
    "workspace.code.commit",
})


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ("git", *args), cwd=root, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "agent@example.invalid")
    _git(root, "config", "user.name", "Runtime Agent")
    (root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / ".env").write_text("API_KEY=must-not-be-read\n", encoding="utf-8")
    _git(root, "add", "app.py")
    _git(root, "commit", "-m", "initial")
    return root


def _provider(root: Path) -> BoundedCodingProvider:
    return BoundedCodingProvider(CodingPolicy(
        repo_root=root,
        test_profiles={
            "unit": ((sys.executable, "-m", "py_compile", "app.py"),),
            "env-clean": ((
                sys.executable,
                "-c",
                "import os,sys;sys.exit(1 if os.getenv('CEREBRAS_API_KEY') else 0)",
            ),),
        },
    ))
def _runtime(tmp_path: Path, provider: BoundedCodingProvider) -> ApplicationRuntime:
    runtime = ApplicationRuntime.bootstrap(
        tmp_path / "runtime",
        subject_id="test-agent",
        max_side_effect_level="LOCAL_STATE",
        allowed_permissions=PERMISSIONS,
    )
    runtime.register_provider(provider)
    return runtime


def test_coding_provider_is_bounded_local_maintenance(tmp_path):
    root = _repo(tmp_path)
    provider = _provider(root)
    manifest = provider.manifest()

    ids = {row["capability_id"] for row in manifest["capabilities"]}
    assert {
        CAP_CODE_INSPECT, CAP_CODE_READ, CAP_CODE_EDIT, CAP_CODE_TEST,
        CAP_CODE_DIFF, CAP_CODE_BRANCH, CAP_CODE_COMMIT,
    }.issubset(ids)
    assert not any("push" in item or "deploy" in item or "merge" in item for item in ids)

    status = provider.invoke(CAP_CODE_INSPECT, {})
    assert status["branch"] == "main"
    assert status["protected_branch"] is True
    assert status["push_available"] is False
    assert status["deploy_available"] is False

    read = provider.invoke(CAP_CODE_READ, {"path": "app.py"})
    assert read["text"] == "VALUE = 1"
def test_coding_provider_rejects_secret_and_escape_paths(tmp_path):
    root = _repo(tmp_path)
    provider = _provider(root)

    with pytest.raises(PermissionError):
        provider.invoke(CAP_CODE_READ, {"path": ".env"})
    with pytest.raises(ValueError):
        provider.invoke(CAP_CODE_READ, {"path": "../outside.py"})

    secret_dir = root / "secrets"
    secret_dir.mkdir()
    (secret_dir / "note.py").write_text("x=1\n", encoding="utf-8")
    with pytest.raises(PermissionError):
        provider.invoke(CAP_CODE_READ, {"path": "secrets/note.py"})


def test_branch_edit_test_commit_requires_current_passing_tests(tmp_path):
    root = _repo(tmp_path)
    provider = _provider(root)
    runtime = _runtime(tmp_path, provider)

    branch = runtime.invoke(CAP_CODE_BRANCH, {"task_id": "fix-value"}).payload
    assert branch["branch"] == "agent/fix-value"

    runtime.invoke(CAP_CODE_EDIT, {
        "path": "app.py",
        "old_text": "VALUE = 1",
        "new_text": "VALUE = 2",
    })
    with pytest.raises(PermissionError, match="passing tests"):
        runtime.invoke(CAP_CODE_COMMIT, {"message": "fix: update value"})

    tested = runtime.invoke(CAP_CODE_TEST, {"profile": "unit"}).payload
    assert tested["ok"] is True

    committed = runtime.invoke(
        CAP_CODE_COMMIT, {"message": "fix: update value"}
    ).payload
    assert committed["pushed"] is False
    assert committed["branch"] == "agent/fix-value"
    assert provider.invoke(CAP_CODE_INSPECT, {})["clean"] is True
    assert _git(root, "show", "HEAD:app.py") == "VALUE = 2"


def test_protected_branch_cannot_commit(tmp_path):
    root = _repo(tmp_path)
    provider = _provider(root)
    (root / "app.py").write_text("VALUE = 3\n", encoding="utf-8")
    assert provider.invoke(CAP_CODE_TEST, {"profile": "unit"})["ok"] is True

    with pytest.raises(PermissionError, match="non-protected"):
        provider.invoke(CAP_CODE_COMMIT, {"message": "fix: forbidden main write"})


def test_test_profile_is_predeclared_and_secret_env_is_removed(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    provider = _provider(root)
    monkeypatch.setenv("CEREBRAS_API_KEY", "not-visible-to-test")
    result = provider.invoke(CAP_CODE_TEST, {"profile": "env-clean"})
    assert result["ok"] is True

    with pytest.raises(ValueError, match="deployment-defined"):
        provider.invoke(CAP_CODE_TEST, {
            "profile": "custom",
            "command": ["sh", "-c", "echo arbitrary"],
        })


def test_edit_invalidates_previous_test_fingerprint(tmp_path):
    root = _repo(tmp_path)
    provider = _provider(root)
    provider.invoke(CAP_CODE_BRANCH, {"task_id": "fingerprint"})
    assert provider.invoke(CAP_CODE_TEST, {"profile": "unit"})["ok"] is True

    provider.invoke(CAP_CODE_EDIT, {
        "path": "app.py",
        "old_text": "VALUE = 1",
        "new_text": "VALUE = 4",
    })
    with pytest.raises(PermissionError, match="passing tests"):
        provider.invoke(CAP_CODE_COMMIT, {"message": "fix: stale test proof"})


def test_runtime_permission_ceiling_still_applies(tmp_path):
    root = _repo(tmp_path)
    provider = _provider(root)
    runtime = ApplicationRuntime.bootstrap(
        tmp_path / "runtime",
        subject_id="test-agent",
        max_side_effect_level="READ_ONLY",
        allowed_permissions=frozenset({"workspace.code.read"}),
    )
    runtime.register_provider(provider)

    assert runtime.invoke(CAP_CODE_READ, {"path": "app.py"}).payload["ok"] is True
    with pytest.raises(PermissionError, match="exceeds runtime ceiling"):
        runtime.invoke(CAP_CODE_BRANCH, {"task_id": "blocked"})


def test_diff_reports_changes_without_external_effects(tmp_path):
    root = _repo(tmp_path)
    provider = _provider(root)
    provider.invoke(CAP_CODE_BRANCH, {"task_id": "diff"})
    provider.invoke(CAP_CODE_EDIT, {
        "path": "app.py",
        "old_text": "VALUE = 1",
        "new_text": "VALUE = 9",
    })
    result = provider.invoke(CAP_CODE_DIFF, {})
    assert "VALUE = 1" in result["diff"]
    assert "VALUE = 9" in result["diff"]
    assert result["changed_paths"] == ["app.py"]
