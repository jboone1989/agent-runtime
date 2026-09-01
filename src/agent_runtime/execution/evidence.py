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
    "api-key",
    "x_api_key",
    "x-api-key",
    "credential",
    "password",
    "private_key",
    "provider_handle",
    "refresh_token",
    "secret",
    "authorization",
    "proxy-authorization",
    "proxy_authorization",
    "signature",
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
_SECRET_VALUE = r'''(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s&"'`]+)'''

_QUOTED_AUTH_HEADER_RE = re.compile(
    r'''(?im)(["'])((?:proxy-)?authorization\s*[:=]\s*(?:[a-z][\w-]*\s+)?)(?:(?:\\.)|(?!\1)[^\r\n])*\1'''
)
_AUTH_HEADER_RE = re.compile(
    r'''(?im)((?:^|[\s"'])(?:proxy-)?authorization\s*[:=]\s*+)((?!(?:[a-z][\w-]*\s+)?\[redacted\])(?:[a-z][\w-]*\s+)?)(?:"[^"]*"|'[^']*'|[^\r\n]+)'''
)
# Key=value assignments: bearer/prefix families cover bare tokens, api keys,
# passwords, and credentials, including UPPER_CASE env forms like
# GITHUB_TOKEN=... and AWS_SESSION_TOKEN=... .
_LABELED_SECRET_RE = re.compile(
    r"(?i)(\b(?:[\w.-]+[_-])?(?:access[_-]?token|refresh[_-]?token"
    r"|session[_-]?token|api[_-]?key|apikey|secret|password|passwd"
    r"|credential|cred|token)[\w.-]*\s*[=:]\s*)" + _SECRET_VALUE
)
_JSON_SECRET_RE = re.compile(
    r'''(?i)(["'](?!input_tokens?["])(?!total_tokens?["])(?:[\w.-]*)(?:authorization|proxy[-_]?authorization|x[-_]?api[-_]?key|api[-_]?key|access[-_]?token|refresh[-_]?token|session[-_]?token|token|apikey|secret|password|passwd|credential|cred)["']\s*:\s*)'''
    + _SECRET_VALUE
)
_JSON_SIGNATURE_RE = re.compile(
    r'''(?i)((?:["'][\w.-]*signature["']\s*:\s*["']))[^\r\n]*$'''
)
_UNTERMINATED_JSON_SECRET_RE = re.compile(
    r'''(?im)((?:["'][\w.-]*(?:authorization|proxy[-_]?authorization|x[-_]?api[-_]?key|api[-_]?key|access[-_]?token|refresh[-_]?token|session[-_]?token|token|apikey|secret|password|passwd|credential|cred|signature)["']\s*:\s*["']))[^\r\n]*$'''
)
_UNTERMINATED_SECRET_RE = re.compile(
    r'''(?im)(\b(?:[\w.-]+[_-])?(?:access[_-]?token|refresh[_-]?token|session[_-]?token|api[_-]?key|apikey|secret|password|passwd|credential|cred|token)[\w.-]*\s*[=:]\s*["'])([^\r\n]*)$'''
)
_UNTERMINATED_CLI_RE = re.compile(
    r'''(?im)((?:^|[\s])--(?:[\w.-]+[_-])?(?:access[_-]?token|refresh[_-]?token|session[_-]?token|api[_-]?key|apikey|secret|password|passwd|credential|cred|token)(?:[_-]\w+)*\s+)["'][^\r\n]*$'''
)
# Space-separated CLI flags (--token VALUE); equals form also lands above.
_CLI_FLAG_RE = re.compile(
    r"(?i)((?:^|[\s])--(?:[\w.-]+[_-])?(?:access[_-]?token|api[_-]?key|api-key|apikey"
    r"|credentials?|password|passwd|secret|token)(?:[_-]\w+)*(?:[=\s]+))"
    + _SECRET_VALUE
)
_QUERY_SECRET_RE = re.compile(
    r"(?i)([?&](?:api[_-]?key|access[_-]?token|token)=)[^&\s\"']+"
)


def bound_execution_evidence(effects: dict[str, Any]) -> dict[str, Any]:
    """Redact secrets, truncate strings, coalesce usage, and cap event count."""
    budget = [_MAX_TOTAL_NODES]
    raw_events = effects.get("backend_events")
    if isinstance(raw_events, (list, tuple)):
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
    events = []
    for index in indices[:-1]:
        events.append(_sanitize_value(_event_mapping(raw_events[index]), 0, budget))
    if indices:
        # Keep the terminal event identifiable even when earlier evidence
        # consumes the shared traversal budget.
        events.append(
            _sanitize_value(_event_mapping(raw_events[indices[-1]]), 0, [256])
        )
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
        for index, (key, item) in enumerate(value.items()):
            if index >= MAX_DICT_ITEMS:
                capped = len(value) - index
                break
            normalized = str(key).lower().replace("-", "_")
            secret_key = any(
                part in normalized for part in _SECRET_KEY_PARTS if part != "token"
            ) or normalized in {"token", "tokens"} or normalized.endswith(
                ("_token", "-token")
            )
            if secret_key:
                continue
            kept += 1
            result[str(key)] = _sanitize_value(item, depth + 1, budget)
        if capped:
            result["_truncated_items"] = capped
        return result
    if isinstance(value, (list, tuple)):
        items: list[Any] = []
        capped = 0
        for item in value[:MAX_LIST_ITEMS]:
            if len(items) >= MAX_LIST_ITEMS:
                break
            items.append(_sanitize_value(item, depth + 1, budget))
        capped = max(0, len(value) - len(items))
        if capped:
            items.append(f"...[{capped} more items truncated]")
        return items
    if isinstance(value, str):
        return _truncate(_redact_string(value))
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _truncate(_redact_string(str(value)))


def _redact_string(text: str) -> str:
    text = _QUOTED_AUTH_HEADER_RE.sub(
        lambda match: match.group(1) + match.group(2) + REDACTION_MARK + match.group(1),
        text,
    )
    text = _AUTH_HEADER_RE.sub(
        lambda match: match.group(1) + match.group(2) + REDACTION_MARK,
        text,
    )
    text = _CLI_FLAG_RE.sub(lambda match: match.group(1) + REDACTION_MARK, text)
    text = _LABELED_SECRET_RE.sub(lambda match: match.group(1) + REDACTION_MARK, text)
    text = _JSON_SECRET_RE.sub(lambda match: match.group(1) + REDACTION_MARK, text)
    text = _JSON_SIGNATURE_RE.sub(lambda match: match.group(1) + REDACTION_MARK, text)
    text = _UNTERMINATED_SECRET_RE.sub(
        lambda match: match.group(1) + REDACTION_MARK,
        text,
    )
    text = _UNTERMINATED_CLI_RE.sub(lambda match: match.group(1) + REDACTION_MARK, text)
    text = _UNTERMINATED_JSON_SECRET_RE.sub(lambda match: match.group(1) + REDACTION_MARK, text)
    text = _QUERY_SECRET_RE.sub(lambda match: match.group(1) + REDACTION_MARK, text)
    return text


def _event_mapping(event: Any) -> Any:
    if isinstance(event, dict):
        return event
    return {
        "kind": getattr(event, "kind", None),
        "summary": getattr(event, "summary", None),
        "payload": getattr(event, "payload", {}),
    }


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


def _bounded_event_indices(events: list[Any] | tuple[Any, ...]) -> tuple[list[int], int]:
    """Select event positions before copying or sanitizing their payloads."""
    last_usage = None
    usage_count = 0
    for index, item in enumerate(events):
        if _event_kind(item) in COALESCED_EVENT_KINDS:
            last_usage = index
            usage_count += 1

    terminal = len(events) - 1 if events else None
    protected = {index for index in (last_usage, terminal) if index is not None}
    selected: list[int] = []
    regular_budget = max(0, MAX_EVENT_COUNT - len(protected))
    for index, item in enumerate(events):
        if index in protected or _event_kind(item) in COALESCED_EVENT_KINDS:
            continue
        if len(selected) >= regular_budget:
            break
        selected.append(index)
    selected.extend(sorted(protected))
    selected.sort()
    coalesced_count = len(events) - max(0, usage_count - 1)
    return selected, max(0, coalesced_count - MAX_EVENT_COUNT)


def _event_kind(event: Any) -> Any:
    if isinstance(event, dict):
        return event.get("kind")
    return getattr(event, "kind", None)
