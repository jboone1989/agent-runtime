"""Isolated Python execution through the E2B Code Interpreter SDK."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ..capabilities.inventory import CapabilityInventory
from ..capabilities.model import (
    CapabilityAccessMode,
    CapabilityBinding,
    CapabilityKind,
    CapabilityLifecycleState,
    SideEffectLevel,
)

SANDBOX_EXECUTE = "sandbox.execute"


@dataclass(frozen=True)
class SandboxPolicy:
    timeout_seconds: float = 30.0
    max_code_chars: int = 100_000
    max_output_chars: int = 100_000
    allow_internet_access: bool = False

    def __post_init__(self) -> None:
        if not 0.1 <= float(self.timeout_seconds) <= 300:
            raise ValueError("sandbox timeout_seconds must be in [0.1, 300]")
        if not 1 <= int(self.max_code_chars) <= 1_000_000:
            raise ValueError("max_code_chars must be in [1, 1000000]")
        if not 1 <= int(self.max_output_chars) <= 1_000_000:
            raise ValueError("max_output_chars must be in [1, 1000000]")


class E2BCodeInterpreterProvider:
    provider_name = "e2b-code-interpreter"
    contract_version = "runtime-sandbox:v1"

    def __init__(
        self,
        *,
        policy: SandboxPolicy | None = None,
        sandbox_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.policy = policy or SandboxPolicy()
        self._sandbox_factory = sandbox_factory

    def manifest(self) -> dict[str, Any]:
        return {
            "provider": self.provider_name,
            "version": self.contract_version,
            "capabilities": [
                {
                    "capability_id": SANDBOX_EXECUTE,
                    "provider": self.provider_name,
                    "version": self.contract_version,
                    "side_effect_level": "EXTERNAL_SIDE_EFFECT",
                    "required_permissions": ["sandbox.execute"],
                }
            ],
        }

    def invoke(
        self, capability_id: str, payload: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        if capability_id != SANDBOX_EXECUTE:
            raise ValueError(f"unsupported sandbox capability: {capability_id}")
        request = dict(payload or {})
        unknown = sorted(set(request) - {"code", "timeout_seconds"})
        if unknown:
            raise ValueError(f"unsupported sandbox parameters: {', '.join(unknown)}")
        code = request.get("code")
        if not isinstance(code, str) or not code or len(code) > self.policy.max_code_chars:
            raise ValueError("sandbox code must be a bounded non-empty string")
        timeout = request.get("timeout_seconds", self.policy.timeout_seconds)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise ValueError("sandbox timeout_seconds must be numeric")
        timeout = float(timeout)
        if not 0.1 <= timeout <= self.policy.timeout_seconds:
            raise ValueError("sandbox timeout_seconds exceeds configured bound")

        sandbox = self._create_sandbox(timeout)
        try:
            execution = sandbox.run_code(code, timeout=timeout)
            logs = getattr(execution, "logs", None)
            stdout = _join_logs(getattr(logs, "stdout", ()))
            stderr = _join_logs(getattr(logs, "stderr", ()))
            text = getattr(execution, "text", None)
            error = getattr(execution, "error", None)
            stdout, stdout_truncated = _bounded(stdout, self.policy.max_output_chars)
            stderr, stderr_truncated = _bounded(stderr, self.policy.max_output_chars)
            text, text_truncated = _bounded(text, self.policy.max_output_chars)
            return {
                "ok": error is None,
                "provider": self.provider_name,
                "capability_id": SANDBOX_EXECUTE,
                "language": "python",
                "stdout": stdout,
                "stdout_truncated": stdout_truncated,
                "stderr": stderr,
                "stderr_truncated": stderr_truncated,
                "result_text": text,
                "result_truncated": text_truncated,
                "error_type": _error_type(error),
                "network_access": bool(self.policy.allow_internet_access),
                "sandbox_ephemeral": True,
            }
        finally:
            try:
                sandbox.kill()
            except Exception:
                pass

    def _create_sandbox(self, timeout: float) -> Any:
        lifetime = max(30, min(600, int(timeout) + 30))
        kwargs = {
            "timeout": lifetime,
            "secure": True,
            "allow_internet_access": bool(self.policy.allow_internet_access),
        }
        if self._sandbox_factory is not None:
            return self._sandbox_factory(**kwargs)
        try:
            from e2b_code_interpreter import Sandbox
        except ImportError as exc:
            raise RuntimeError("sandbox extra missing; install agent-runtime[sandbox]") from exc
        return Sandbox.create(**kwargs)


def register_sandbox_provider(
    provider: E2BCodeInterpreterProvider, inventory: CapabilityInventory
) -> tuple[str, ...]:
    if inventory.get(SANDBOX_EXECUTE) is not None:
        raise ValueError(f"sandbox capability already registered: {SANDBOX_EXECUTE}")
    inventory.register(
        CapabilityBinding(
            capability_id=SANDBOX_EXECUTE,
            kind=CapabilityKind.SERVICE,
            provider=provider.provider_name,
            candidate_id=f"{provider.provider_name}:{SANDBOX_EXECUTE}",
            access_mode=CapabilityAccessMode.REMOTE_ACCESS,
            state=CapabilityLifecycleState.ACTIVE,
            version=provider.contract_version,
            side_effects=SideEffectLevel.EXTERNAL_SIDE_EFFECT,
            entry_point={
                "adapter": "E2BCodeInterpreterProvider",
                "required_permissions": ["sandbox.execute"],
            },
            evidence_refs=["contract:runtime-sandbox:v1"],
        )
    )
    return (SANDBOX_EXECUTE,)


def _join_logs(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return "".join(str(item) for item in value)
    return "" if value is None else str(value)


def _bounded(value: Any, limit: int) -> tuple[str, bool]:
    text = "" if value is None else str(value)
    return text[:limit], len(text) > limit


def _error_type(value: Any) -> str | None:
    if value is None:
        return None
    name = getattr(value, "name", None)
    if isinstance(name, str) and name:
        return name[:256]
    return type(value).__name__[:256]


__all__ = [
    "E2BCodeInterpreterProvider",
    "SANDBOX_EXECUTE",
    "SandboxPolicy",
    "register_sandbox_provider",
]
