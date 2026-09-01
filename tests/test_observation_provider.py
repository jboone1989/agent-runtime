from __future__ import annotations

from datetime import datetime, timezone
import os

from dulwich import porcelain
import httpx
import pytest

from agent_runtime.capabilities import (
    CapabilityInventory,
    CapabilityRequirement,
    ReadCapabilityGateway,
)
from agent_runtime.observation import (
    BUILTIN_OBSERVATION_CAPABILITIES,
    CLOCK_OBSERVE,
    ENVIRONMENT_OBSERVE,
    GIT_OBSERVE,
    HTTP_OBSERVE,
    PROCESS_OBSERVE,
    SYSTEM_OBSERVE,
    WORKSPACE_OBSERVE,
    ObservationResult,
    RuntimeObservationProvider,
    register_observation_provider,
)


MOMENT = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def _provider(tmp_path, **kwargs) -> RuntimeObservationProvider:
    return RuntimeObservationProvider(
        workspace_roots={"test": tmp_path},
        git_roots={"test": tmp_path},
        disk_path=tmp_path,
        wall_clock=lambda: MOMENT,
        monotonic_ns=lambda: 123456,
        **kwargs,
    )


def _observe(provider, capability_id, payload=None) -> ObservationResult:
    return ObservationResult.from_dict(provider.invoke(capability_id, payload or {}))


def test_observation_contract_is_strict_json_and_defensively_copied() -> None:
    data = {"items": [1]}
    result = ObservationResult("provider", "observation.test", MOMENT, data, {})
    data["items"].append(2)

    assert result.to_dict()["data"] == {"items": [1]}
    with pytest.raises(ValueError, match="strict JSON"):
        ObservationResult("provider", "observation.test", MOMENT, {"bad": float("nan")}, {})
    raw = result.to_dict()
    raw["ok"] = False
    with pytest.raises(ValueError, match="ok flag"):
        ObservationResult.from_dict(raw)


def test_bundle_registers_in_existing_inventory_and_crosses_read_gateway(tmp_path) -> None:
    provider = _provider(tmp_path)
    inventory = CapabilityInventory()

    registered = register_observation_provider(provider, inventory)
    invocation = ReadCapabilityGateway(inventory, provider).invoke(
        CapabilityRequirement(CLOCK_OBSERVE)
    )

    assert registered == BUILTIN_OBSERVATION_CAPABILITIES
    assert invocation.accepted is True
    observation = ObservationResult.from_dict(invocation.result)
    assert observation.ok is True
    assert observation.data == {"wall_time": "2026-09-01T12:00:00.000000Z", "monotonic_ns": 123456}
    assert inventory.get(CLOCK_OBSERVE).side_effects.name == "READ_ONLY"


def test_workspace_operations_are_root_bound_bounded_and_do_not_follow_redirects(tmp_path) -> None:
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "note.txt").write_text("hello", encoding="utf-8")
    (tmp_path / "binary.bin").write_bytes(b"\xff\x00")
    provider = _provider(tmp_path, max_depth=2, max_entries=8, max_bytes=4)

    listing = _observe(provider, WORKSPACE_OBSERVE, {"root_id": "test", "operation": "list"})
    tree = _observe(provider, WORKSPACE_OBSERVE, {"root_id": "test", "operation": "tree"})
    text = _observe(provider, WORKSPACE_OBSERVE, {"root_id": "test", "operation": "read", "path": "nested/note.txt"})
    binary = _observe(provider, WORKSPACE_OBSERVE, {"root_id": "test", "operation": "read", "path": "binary.bin"})
    escaped = _observe(provider, WORKSPACE_OBSERVE, {"root_id": "test", "operation": "stat", "path": "../outside"})

    assert [item["path"] for item in listing.data["entries"]] == ["binary.bin", "nested"]
    assert [item["path"] for item in tree.data["entries"]] == ["binary.bin", "nested", "nested/note.txt"]
    assert text.data["content"] == "hell"
    assert text.data["truncated"] is True
    assert binary.data["encoding"] == "base64"
    assert escaped.error["code"] == "path_outside_root"

    try:
        os.symlink(tmp_path / "nested", tmp_path / "redirect", target_is_directory=True)
    except (OSError, NotImplementedError):
        return
    redirected = _observe(provider, WORKSPACE_OBSERVE, {"root_id": "test", "operation": "list", "path": "redirect"})
    assert redirected.error["code"] == "redirect_blocked"


def test_workspace_entry_and_depth_limits_fail_explicitly(tmp_path) -> None:
    (tmp_path / "a").write_text("a", encoding="utf-8")
    (tmp_path / "b").write_text("b", encoding="utf-8")
    provider = _provider(tmp_path, max_entries=1, max_depth=1)

    too_many = _observe(provider, WORKSPACE_OBSERVE, {"operation": "list", "root_id": "test"})
    too_deep = _observe(provider, WORKSPACE_OBSERVE, {"operation": "tree", "root_id": "test", "max_depth": 2})

    assert too_many.error["code"] == "entry_limit_exceeded"
    assert too_deep.error["code"] == "invalid_max_depth"


def test_git_observation_uses_dulwich_and_returns_structured_state(tmp_path) -> None:
    repo = porcelain.init(tmp_path)
    (tmp_path / "tracked.txt").write_text("one\n", encoding="utf-8")
    (tmp_path / "deleted.txt").write_text("delete me\n", encoding="utf-8")
    porcelain.add(repo, ["tracked.txt", "deleted.txt"])
    commit_id = porcelain.commit(repo, message=b"initial", author=b"Test <test@example.invalid>")
    (tmp_path / "tracked.txt").write_text("two\n", encoding="utf-8")
    (tmp_path / "staged.txt").write_text("staged\n", encoding="utf-8")
    porcelain.add(repo, ["staged.txt"])
    porcelain.rm(repo, ["deleted.txt"])
    (tmp_path / "untracked.txt").write_text("new\n", encoding="utf-8")
    repo.close()

    observation = _observe(_provider(tmp_path), GIT_OBSERVE, {"root_id": "test"})

    assert observation.ok is True
    assert observation.data["repo_root"] == str(tmp_path)
    assert observation.data["head_sha"] == commit_id.decode("ascii")
    assert observation.data["branch"] == "master"
    assert observation.data["detached"] is False
    assert observation.data["staged"] == ["deleted.txt", "staged.txt"]
    assert observation.data["modified"] == ["tracked.txt"]
    assert observation.data["deleted"] == ["deleted.txt"]
    assert observation.data["untracked"] == ["untracked.txt"]
    assert observation.evidence["library"] == "dulwich"
    assert observation.evidence["git_cli_invoked"] is False


def test_environment_is_allowlisted_and_includes_python_runtime(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SAFE_RUNTIME_VALUE", "visible")
    monkeypatch.setenv("SECRET_TOKEN", "hidden")
    provider = _provider(tmp_path, environment_allowlist=("SAFE_RUNTIME_VALUE",))

    allowed = _observe(provider, ENVIRONMENT_OBSERVE, {"names": ["SAFE_RUNTIME_VALUE"]})
    denied = _observe(provider, ENVIRONMENT_OBSERVE, {"names": ["SECRET_TOKEN"]})

    assert allowed.data["environment"] == {"SAFE_RUNTIME_VALUE": "visible"}
    assert allowed.data["python"]["version"]
    assert denied.error["code"] == "environment_name_not_allowed"
    assert "hidden" not in str(denied.to_dict())


def test_failure_evidence_is_bounded_and_stays_inside_result(tmp_path) -> None:
    provider = _provider(tmp_path, environment_allowlist=())
    oversized_name = "X" * (1_100_000)
    result = _observe(provider, ENVIRONMENT_OBSERVE, {"names": [oversized_name]})

    assert result.ok is False
    assert result.error["code"] == "environment_name_not_allowed"
    assert len(str(result.to_dict()).encode("utf-8")) < 20_000


def test_system_and_process_observations_are_bounded(tmp_path) -> None:
    provider = _provider(tmp_path, max_processes=2)

    system = _observe(provider, SYSTEM_OBSERVE)
    current = _observe(provider, PROCESS_OBSERVE)
    processes = _observe(provider, PROCESS_OBSERVE, {"operation": "list", "limit": 2})
    excessive = _observe(provider, PROCESS_OBSERVE, {"operation": "list", "limit": 3})

    assert system.ok is True
    assert system.data["memory"]["total"] > 0
    assert current.data["process"]["pid"] == os.getpid()
    assert len(processes.data["processes"]) <= 2
    assert excessive.error["code"] == "invalid_limit"


def test_http_supports_only_bounded_read_methods_and_same_origin_redirects(tmp_path) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/redirect":
            return httpx.Response(302, headers={"location": "/data", "set-cookie": "secret=1"})
        return httpx.Response(200, headers={"content-type": "application/json", "set-cookie": "secret=1"}, content=b'{"ok":true}')

    provider = _provider(
        tmp_path,
        http_allowed_hosts=("example.test",),
        http_redirect_policy="same-origin",
        http_transport=httpx.MockTransport(handle),
    )

    result = _observe(provider, HTTP_OBSERVE, {"url": "https://example.test/redirect", "follow_redirects": True})
    mutation = _observe(provider, HTTP_OBSERVE, {"url": "https://example.test/data", "method": "POST"})
    foreign = _observe(provider, HTTP_OBSERVE, {"url": "https://other.test/data"})

    assert result.ok is True
    assert result.data["status_code"] == 200
    assert result.data["redirects_followed"] == 1
    assert "set-cookie" not in result.data["headers"]
    assert mutation.error["code"] == "method_not_allowed"
    assert foreign.error["code"] == "url_not_allowed"


def test_http_response_size_limit_is_explicit(tmp_path) -> None:
    provider = _provider(
        tmp_path,
        max_bytes=4,
        http_allowed_hosts=("example.test",),
        http_transport=httpx.MockTransport(lambda _request: httpx.Response(200, content=b"12345")),
    )

    result = _observe(provider, HTTP_OBSERVE, {"url": "https://example.test/data"})

    assert result.error["code"] == "response_too_large"
    assert result.evidence == {"max_bytes": 4}
