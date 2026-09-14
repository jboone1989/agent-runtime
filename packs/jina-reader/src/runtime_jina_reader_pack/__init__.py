"""Jina Reader capability pack for Agent Runtime."""
from __future__ import annotations

from collections.abc import Mapping
import math
from typing import Any

from .provider import JinaReaderProvider, WEB_FETCH

PACK_ID = "jina-reader"
PACK_VERSION = "1.0.0"


class JinaReaderPack:
    pack_id = PACK_ID
    version = PACK_VERSION

    def manifest(self) -> dict[str, Any]:
        return {
            "pack_id": self.pack_id,
            "version": self.version,
            "provider": "jina-reader",
            "license": "Apache-2.0",
            "capabilities": [{
                "capability_id": WEB_FETCH,
                "description": "Fetch one bounded public page through Jina Reader.",
                "kind": "SERVICE",
                "side_effect_level": "READ_ONLY",
                "required_permissions": [],
                "maintained": True,
                "task_fit": 0.95,
            }],
        }

    def build_provider(self, config: Mapping[str, Any] | None = None) -> JinaReaderProvider:
        values = dict(config or {})
        allowed = {
            "reader_base_url", "api_key", "timeout_seconds", "max_chars", "allow_insecure_http",
        }
        unknown = sorted(set(values) - allowed)
        if unknown:
            raise ValueError(f"unknown Jina Reader pack config: {unknown[0]}")
        if "allow_insecure_http" in values and not isinstance(values["allow_insecure_http"], bool):
            raise ValueError("allow_insecure_http must be boolean")
        if "api_key" in values and values["api_key"] is not None and not isinstance(values["api_key"], str):
            raise ValueError("api_key must be a string")
        if "timeout_seconds" in values:
            timeout = values["timeout_seconds"]
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
                raise ValueError("timeout_seconds must be positive and finite")
            values["timeout_seconds"] = float(timeout)
        return JinaReaderProvider(**values)


def build_pack() -> JinaReaderPack:
    return JinaReaderPack()


__all__ = [
    "PACK_ID", "PACK_VERSION", "JinaReaderPack", "JinaReaderProvider", "WEB_FETCH", "build_pack",
]
