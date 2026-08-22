"""Secret stripping and content digests. Never persist credentials."""
from __future__ import annotations

import hashlib
import json
from typing import Any

_SECRET_KEYS = frozenset({"api_key", "secret", "token", "password", "provider_handle"})


def strip_secrets(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {
            key: strip_secrets(value)
            for key, value in obj.items()
            if str(key).lower() not in _SECRET_KEYS and "secret" not in str(key).lower()
        }
    if isinstance(obj, list):
        return [strip_secrets(item) for item in obj]
    return obj


def content_digest(files: dict[str, Any], version: str | None) -> str:
    payload = {"files": files, "version": version}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
