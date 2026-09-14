from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_runtime.application import ApplicationRuntime
from agent_runtime.capabilities.inventory import CapabilityInventory
from agent_runtime.capabilities.model import SideEffectLevel
from agent_runtime.integrations.sandbox import (
    E2BCodeInterpreterProvider,
    SANDBOX_EXECUTE,
    SandboxPolicy,
    register_sandbox_provider,
)


class _Sandbox:
    def __init__(self, execution):
        self.execution = execution
        self.calls = []
        self.killed = False

    def run_code(self, code, timeout=None):
        self.calls.append((code, timeout))
        return self.execution

    def kill(self):
        self.killed = True


def _execution(*, text="2", stdout=("hello\n",), stderr=(), error=None):
    return SimpleNamespace(
        text=text,
        logs=SimpleNamespace(stdout=list(stdout), stderr=list(stderr)),
        error=error,
    )


def test_sandbox_manifest_and_inventory_require_explicit_authority() -> None:
    provider = E2BCodeInterpreterProvider(sandbox_factory=lambda **kwargs: _Sandbox(_execution()))
    inventory = CapabilityInventory()

    assert register_sandbox_provider(provider, inventory) == (SANDBOX_EXECUTE,)
    binding = inventory.get(SANDBOX_EXECUTE)
    assert binding.side_effects is SideEffectLevel.EXTERNAL_SIDE_EFFECT
    assert binding.entry_point["required_permissions"] == ["sandbox.execute"]
    assert binding.access_mode.value == "REMOTE_ACCESS"


def test_sandbox_executes_only_bounded_python_and_kills_ephemeral_session() -> None:
    created = []

    def factory(**kwargs):
        sandbox = _Sandbox(_execution(text="42", stdout=("done\n",)))
        created.append((kwargs, sandbox))
        return sandbox

    provider = E2BCodeInterpreterProvider(
        policy=SandboxPolicy(timeout_seconds=5, allow_internet_access=False),
        sandbox_factory=factory,
    )
    result = provider.invoke(SANDBOX_EXECUTE, {"code": "6 * 7", "timeout_seconds": 2})

    assert result["ok"] is True
    assert result["language"] == "python"
    assert result["result_text"] == "42"
    assert result["network_access"] is False
    kwargs, sandbox = created[0]
    assert kwargs["secure"] is True
    assert kwargs["allow_internet_access"] is False
    assert sandbox.calls == [("6 * 7", 2.0)]
    assert sandbox.killed is True


def test_sandbox_output_is_bounded_and_error_name_is_normalized() -> None:
    error = SimpleNamespace(name="ValueError")
    sandbox = _Sandbox(_execution(text="abcdef", stdout=("123456",), stderr=("xyz",), error=error))
    provider = E2BCodeInterpreterProvider(
        policy=SandboxPolicy(max_output_chars=3),
        sandbox_factory=lambda **kwargs: sandbox,
    )

    result = provider.invoke(SANDBOX_EXECUTE, {"code": "raise ValueError()"})

    assert result["ok"] is False
    assert result["result_text"] == "abc"
    assert result["result_truncated"] is True
    assert result["stdout"] == "123"
    assert result["stdout_truncated"] is True
    assert result["error_type"] == "ValueError"
    assert sandbox.killed is True


def test_sandbox_rejects_extra_controls_and_code_budget() -> None:
    provider = E2BCodeInterpreterProvider(
        policy=SandboxPolicy(max_code_chars=4),
        sandbox_factory=lambda **kwargs: _Sandbox(_execution()),
    )
    with pytest.raises(ValueError, match="unsupported sandbox parameters"):
        provider.invoke(SANDBOX_EXECUTE, {"code": "1", "command": "x"})
    with pytest.raises(ValueError, match="bounded"):
        provider.invoke(SANDBOX_EXECUTE, {"code": "12345"})


def test_application_runtime_enforces_sandbox_permission_and_side_effect_gate(tmp_path) -> None:
    denied = E2BCodeInterpreterProvider(sandbox_factory=lambda **kwargs: _Sandbox(_execution()))
    runtime = ApplicationRuntime.bootstrap(
        tmp_path,
        subject_id="subject",
        max_side_effect_level="EXTERNAL_SIDE_EFFECT",
    )
    runtime.register_provider(denied)
    with pytest.raises(PermissionError, match="missing capability permissions"):
        runtime.invoke(SANDBOX_EXECUTE, {"code": "1 + 1"})

    allowed = E2BCodeInterpreterProvider(sandbox_factory=lambda **kwargs: _Sandbox(_execution()))
    runtime2 = ApplicationRuntime.bootstrap(
        tmp_path / "allowed",
        subject_id="subject",
        max_side_effect_level="EXTERNAL_SIDE_EFFECT",
        allowed_permissions=frozenset({"sandbox.execute"}),
    )
    runtime2.register_provider(allowed)
    receipt = runtime2.invoke(SANDBOX_EXECUTE, {"code": "1 + 1"})
    assert receipt.payload["ok"] is True
    assert receipt.provider == "e2b-code-interpreter"
