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
    "token",
    "apikey",
    "passwd",
)

MAX_EVENT_COUNT = 200
MAX_STRING_CHARS = 4000
MAX_LIST_ITEMS = 64
MAX_DICT_ITEMS = 64
MAX_DEPTH = 8
_MAX_TOTAL_NODES = 20_000

TRUNCATION_MARK = "...[truncated]"
REDACTION_MARK = "[redacted]"
DEPTH_MARK = "...[depth limit reached]"
BUDGET_MARK = "...[evidence budget exhausted]"

# Event kinds whose payload is a cumulative snapshot; only the last one is
# evidence, earlier occurrences are progress noise.
COALESCED_EVENT_KINDS = frozenset({"backend.usage.updated"})

# Secret values end at whitespace or quoting so surrounding command shapes
# (closing quotes, URLs, flags) stay auditable.
_SECRET_VALUE = r'''(?:"[^"]*"|'[^']*'|[^\s&"'`]+)'''

_AUTH_HEADER_RE = re.compile(
    r"(?i)((?:proxy-)?authorization\s*[:=]\s*(?:bearer|basic|token)\s+)" + _SECRET_VALUE
)
# Key=value assignments: bearer/prefix families cover bare tokens, api keys,
# passwords, and credentials, including UPPER_CASE env forms like
# GITHUB_TOKEN=... and AWS_SESSION_TOKEN=... .
_LABELED_SECRET_RE = re.compile(
    r"(?i)(\b(?:[\w.-]+[_-])?(?:access[_-]?token|refresh[_-]?token"
    r"|session[_-]?token|api[_-]?key|apikey|secret|password|passwd"
    r"|credential|cred|token)[\w.-]*\s*[=:]\s*[\"']?)" + _SECRET_VALUE
)
# Space-separated CLI flags (--token VALUE); equals form also lands above.
_CLI_FLAG_RE = re.compile(
    r"(?i)((?:^|[\s])--(?:[\w.-]+[_-])?(?:access[_-]?token|api[_-]?key|api-key|apikey"
    r"|credentials?|password|passwd|secret|token)(?:[_-]\w+)*(?:[=\s]+)[\"']?)"
    + _SECRET_VALUE
)
_QUERY_SECRET_RE = re.compile(
    r"(?i)([?&](?:api[_-]?key|access[_-]?token|token)=)[^&\s\"']+"
)


def bound_execution_evidence(effects: dict[str, Any]) -> dict[str, Any]:
    """Redact secrets, truncate strings, coalesce usage, and cap event count."""
    budget = [_MAX_TOTAL_NODES]
    raw_events = effects.get("backend_events")
    if isinstance(raw_events, list):
        candidates = {
            key: value for key, value in effects.items() if key != "backend_events"
        }
    else:
        raw_events = None
        candidates = effects
    bounded = _sanitize_value(candidates, 0, budget)
    if raw_events is None:
        return bounded
    # backend_events is a list of audit records, not one generic collection:
    # sanitize each event on its own so a long turn keeps its trail up to
    # MAX_EVENT_COUNT while every payload stays individually bounded.
    indices, dropped = _bounded_event_indices(raw_events)
    events = [_sanitize_value(raw_events[index], 0, budget) for index in indices]
    bounded["backend_events"] = events
    bounded["events_truncated"] = dropped > 0
    bounded["events_dropped"] = dropped
    return bounded


def _sanitize_value(value: Any, depth: int, budget: list[int]) -> Any:
    budget[0] -= 1
    if budget[0] <= 0:
        return BUDGET_MARK
    if depth >= MAX_DEPTH:
        return DEPTH_MARK
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        kept = 0
        capped = 0
        for key, item in value.items():
            normalized = str(key).lower()
            secret_key = any(
                part in normalized for part in _SECRET_KEY_PARTS if part != "token"
            ) or normalized in {"token", "tokens"} or normalized.endswith(
                ("_token", "-token")
            )
            if secret_key:
                continue
            if kept >= MAX_DICT_ITEMS:
                capped += 1
                continue
            kept += 1
            result[str(key)] = _sanitize_value(item, depth + 1, budget)
        if capped:
            result["_truncated_items"] = capped
        return result
    if isinstance(value, (list, tuple)):
        items: list[Any] = []
        capped = 0
        for item in value:
            if len(items) >= MAX_LIST_ITEMS:
                capped += 1
                continue
            items.append(_sanitize_value(item, depth + 1, budget))
        if capped:
            items.append(f"...[{capped} more items truncated]")
        return items
    if isinstance(value, str):
        return _truncate(_redact_string(value))
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _truncate(_redact_string(str(value)))


def _redact_string(text: str) -> str:
    text = _AUTH_HEADER_RE.sub(lambda match: match.group(1) + REDACTION_MARK, text)
    text = _CLI_FLAG_RE.sub(lambda match: match.group(1) + REDACTION_MARK, text)
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


def _bounded_event_indices(events: list[Any]) -> tuple[list[int], int]:
    """Select event positions before copying or sanitizing their payloads."""
    last_usage = None
    usage_count = 0
    for index, item in enumerate(events):
        if isinstance(item, dict) and item.get("kind") in COALESCED_EVENT_KINDS:
            last_usage = index
            usage_count += 1

    selected: list[int] = []
    terminal = None
    for index, item in enumerate(events):
        if isinstance(item, dict) and item.get("kind") in COALESCED_EVENT_KINDS:
            if index != last_usage:
                continue
        if len(selected) < MAX_EVENT_COUNT - 1:
            selected.append(index)
        else:
            terminal = index
    if terminal is not None:
        selected.append(terminal)

    coalesced_count = len(events) - max(0, usage_count - 1)
    return selected, max(0, coalesced_count - MAX_EVENT_COUNT)
