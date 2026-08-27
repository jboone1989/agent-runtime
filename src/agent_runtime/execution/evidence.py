"""Redact and bound backend evidence before it becomes durable Runtime records.

The persistence boundary lives here, not in the backend adapters: every
specialist's events, response text, and verification details cross this single
gate on their way into ``OutcomeRecord.effects``. Capability Fabric rule 24 —
secrets never enter Runtime snapshots or skill memory — is enforced for all
current and future backends at once.
"""
from __future__ import annotations

import re
from typing import Any

# Mirrors context.projection._SECRET_KEY_PARTS: dict keys carrying these
# fragments are dropped entirely from durable evidence.
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

MAX_EVENT_COUNT = 200
MAX_STRING_CHARS = 4000
TRUNCATION_MARK = "...[truncated]"
REDACTION_MARK = "[redacted]"

# Event kinds whose payload is a cumulative snapshot; only the last one is
# evidence, earlier occurrences are progress noise.
COALESCED_EVENT_KINDS = frozenset({"backend.usage.updated"})

# Secret values end at whitespace or quoting so surrounding command shapes
# (closing quotes, URLs) stay auditable.
_SECRET_VALUE = r"[^\s\"'`]+"
_AUTH_HEADER_RE = re.compile(
    r"(?i)((?:proxy-)?authorization\s*[:=]\s*(?:bearer|basic|token)?\s*)" + _SECRET_VALUE
)
_LABELED_SECRET_RE = re.compile(
    r"(?i)((?:api[_-]?key|access[_-]?token|refresh[_-]?token|secret"
    r"|password|credential)[a-z0-9_.-]*\s*[:=]\s*[\"']?)" + _SECRET_VALUE
)
_QUERY_SECRET_RE = re.compile(
    r"(?i)([?&](?:api[_-]?key|access[_-]?token|token)=)[^&\s\"']+"
)


def bound_execution_evidence(effects: dict[str, Any]) -> dict[str, Any]:
    """Redact secrets, truncate strings, coalesce usage, and cap event count."""
    bounded = _sanitize_value(effects)
    events = bounded.get("backend_events")
    if isinstance(events, list):
        events = _coalesce_usage_events(events)
        dropped = max(0, len(events) - MAX_EVENT_COUNT)
        if dropped:
            # Keep the earliest side effects plus the terminal event; the
            # middle progress record is the lowest-value evidence to lose.
            events = events[: MAX_EVENT_COUNT - 1] + [events[-1]]
        bounded["backend_events"] = events
        bounded["events_truncated"] = dropped > 0
        bounded["events_dropped"] = dropped
    return bounded


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).lower()
            if any(part in normalized for part in _SECRET_KEY_PARTS):
                continue
            result[str(key)] = _sanitize_value(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, str):
        return _truncate(_redact_string(value))
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _truncate(_redact_string(str(value)))


def _redact_string(text: str) -> str:
    text = _AUTH_HEADER_RE.sub(lambda match: match.group(1) + REDACTION_MARK, text)
    text = _LABELED_SECRET_RE.sub(lambda match: match.group(1) + REDACTION_MARK, text)
    text = _QUERY_SECRET_RE.sub(lambda match: match.group(1) + REDACTION_MARK, text)
    return text


def _truncate(text: str) -> str:
    if len(text) <= MAX_STRING_CHARS:
        return text
    keep = MAX_STRING_CHARS - len(TRUNCATION_MARK)
    return text[:keep] + TRUNCATION_MARK


def _coalesce_usage_events(events: list[Any]) -> list[Any]:
    last_index: dict[str, int] = {}
    for index, item in enumerate(events):
        kind = item.get("kind") if isinstance(item, dict) else None
        if kind in COALESCED_EVENT_KINDS:
            last_index[kind] = index
    kept = []
    for index, item in enumerate(events):
        kind = item.get("kind") if isinstance(item, dict) else None
        if kind in COALESCED_EVENT_KINDS and last_index[kind] != index:
            continue
        kept.append(item)
    return kept
