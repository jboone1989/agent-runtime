"""Select, redact, and bound Runtime state before it reaches a backend."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ..backends.model import AgentRunMode

_SECRET_KEY_PARTS = (
    "access_token",
    "api_key",
    "credential",
    "password",
    "private_key",
    "provider_handle",
    "refresh_token",
    "secret",
)


@dataclass(frozen=True)
class ContextSnapshot:
    """Complete candidate sections before disclosure policy is applied."""

    sections: dict[str, Any]


@dataclass(frozen=True)
class ContextPolicy:
    """Stable disclosure and size policy for one backend request."""

    allowed_sections: frozenset[str] | None = None
    section_priority: tuple[str, ...] = ()
    max_items_per_list: int = 32
    max_string_chars: int = 4000
    max_serialized_chars: int = 60_000

    def __post_init__(self) -> None:
        if self.max_items_per_list < 1:
            raise ValueError("max_items_per_list must be positive")
        if self.max_string_chars < 16:
            raise ValueError("max_string_chars must be at least 16")
        if self.max_serialized_chars < 256:
            raise ValueError("max_serialized_chars must be at least 256")


@dataclass(frozen=True)
class BoundedRuntimeContext:
    schema_version: str
    mode: AgentRunMode
    sections: dict[str, Any]
    omitted_sections: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "mode": self.mode.value,
            "sections": self.sections,
            "omitted_sections": list(self.omitted_sections),
        }


class ContextProjector:
    """Pure projection; it cannot read Runtime state or grant authority."""

    def project(
        self,
        snapshot: ContextSnapshot,
        *,
        mode: AgentRunMode,
        policy: ContextPolicy | None = None,
    ) -> BoundedRuntimeContext:
        selected_policy = policy or ContextPolicy()
        allowed = selected_policy.allowed_sections
        candidate_names = [
            name
            for name in snapshot.sections
            if allowed is None or name in allowed
        ]
        priority = list(selected_policy.section_priority)
        ordered_names = [name for name in priority if name in candidate_names]
        ordered_names.extend(name for name in candidate_names if name not in ordered_names)

        sections: dict[str, Any] = {}
        omitted = [
            name for name in snapshot.sections if allowed is not None and name not in allowed
        ]
        for name in ordered_names:
            value = _sanitize(snapshot.sections[name], selected_policy)
            trial = BoundedRuntimeContext("1.0", mode, {**sections, name: value}, tuple(omitted))
            if _serialized_size(trial.to_dict()) <= selected_policy.max_serialized_chars:
                sections[name] = value
            else:
                omitted.append(name)

        return BoundedRuntimeContext(
            schema_version="1.0",
            mode=mode,
            sections=sections,
            omitted_sections=tuple(omitted),
        )


def _sanitize(value: Any, policy: ContextPolicy) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).lower()
            if any(part in normalized for part in _SECRET_KEY_PARTS):
                continue
            result[str(key)] = _sanitize(item, policy)
        return result
    if isinstance(value, (list, tuple)):
        return [
            _sanitize(item, policy)
            for item in value[-policy.max_items_per_list :]
        ]
    if isinstance(value, set):
        return [
            _sanitize(item, policy)
            for item in sorted(value, key=str)[: policy.max_items_per_list]
        ]
    if isinstance(value, str) and len(value) > policy.max_string_chars:
        keep = policy.max_string_chars - len("...[truncated]")
        return value[:keep] + "...[truncated]"
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def _serialized_size(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=False, sort_keys=True))
