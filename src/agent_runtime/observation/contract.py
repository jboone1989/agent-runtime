"""Runtime-owned result contract for bounded external observations."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Any, Mapping


def _timestamp(value: object) -> str:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("observed_at must be an ISO-8601 timestamp") from exc
    else:
        raise ValueError("observed_at must be a timezone-aware timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("observed_at must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _json_object(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be a JSON object")
    try:
        return json.loads(
            json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be strict JSON") from exc


@dataclass(frozen=True)
class ObservationResult:
    """JSON-safe provider result; success is distinct from invocation acceptance."""

    provider_id: str
    capability_id: str
    observed_at: str | datetime
    data: Mapping[str, Any]
    evidence: Mapping[str, Any]
    error: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        for field in ("provider_id", "capability_id"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field} must be non-empty")
        object.__setattr__(self, "observed_at", _timestamp(self.observed_at))
        object.__setattr__(self, "data", _json_object(self.data, "data"))
        object.__setattr__(self, "evidence", _json_object(self.evidence, "evidence"))
        if self.error is not None:
            error = _json_object(self.error, "error")
            code = error.get("code")
            if not isinstance(code, str) or not code.strip():
                raise ValueError("error.code must be non-empty")
            object.__setattr__(self, "error", error)

    @property
    def ok(self) -> bool:
        return self.error is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1",
            "provider_id": self.provider_id,
            "capability_id": self.capability_id,
            "observed_at": self.observed_at,
            "ok": self.ok,
            "data": deepcopy(self.data),
            "evidence": deepcopy(self.evidence),
            "error": deepcopy(self.error),
        }

    @classmethod
    def from_dict(cls, raw: object) -> "ObservationResult":
        if not isinstance(raw, Mapping):
            raise ValueError("observation result must be an object")
        expected = {
            "schema_version",
            "provider_id",
            "capability_id",
            "observed_at",
            "ok",
            "data",
            "evidence",
            "error",
        }
        if set(raw) != expected or raw.get("schema_version") != "1":
            raise ValueError("observation result schema mismatch")
        result = cls(
            provider_id=raw.get("provider_id"),
            capability_id=raw.get("capability_id"),
            observed_at=raw.get("observed_at"),
            data=raw.get("data"),
            evidence=raw.get("evidence"),
            error=raw.get("error"),
        )
        if raw.get("ok") is not result.ok:
            raise ValueError("observation result ok flag mismatch")
        return result


class ObservationFailure(RuntimeError):
    """Expected provider failure that can safely cross the invocation boundary."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        evidence: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message[:500]
        self.evidence = dict(evidence or {})
