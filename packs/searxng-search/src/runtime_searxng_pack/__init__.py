"""SearXNG capability pack for Agent Runtime."""
from __future__ import annotations

from collections.abc import Mapping
import math
from typing import Any

from .provider import SearXNGSearchProvider, WEB_SEARCH

PACK_ID = "searxng-search"
PACK_VERSION = "1.0.0"


class SearXNGSearchPack:
    pack_id = PACK_ID
    version = PACK_VERSION

    def manifest(self) -> dict[str, Any]:
        return {
            "pack_id": self.pack_id,
            "version": self.version,
            "provider": "searxng",
            "license": "MIT",
            "capabilities": [{
                "capability_id": WEB_SEARCH,
                "description": "Search the public web through SearXNG.",
                "kind": "SERVICE",
                "side_effect_level": "READ_ONLY",
                "required_permissions": [],
                "maintained": True,
                "task_fit": 0.95,
            }],
        }
    def build_provider(
        self, config: Mapping[str, Any] | None = None
    ) -> SearXNGSearchProvider:
        values = dict(config or {})
        allowed = {
            "base_url",
            "timeout_seconds",
            "allow_insecure_http",
            "max_results",
            "max_query_chars",
        }
        unknown = sorted(set(values) - allowed)
        if unknown:
            raise ValueError(f"unknown SearXNG pack config: {unknown[0]}")
        if "allow_insecure_http" in values and not isinstance(values["allow_insecure_http"], bool):
            raise ValueError("allow_insecure_http must be boolean")
        if "timeout_seconds" in values:
            timeout = values["timeout_seconds"]
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
                raise ValueError("timeout_seconds must be positive and finite")
            values["timeout_seconds"] = float(timeout)
        return SearXNGSearchProvider(**values)


def build_pack() -> SearXNGSearchPack:
    return SearXNGSearchPack()


__all__ = [
    "PACK_ID",
    "PACK_VERSION",
    "SearXNGSearchPack",
    "SearXNGSearchProvider",
    "WEB_SEARCH",
    "build_pack",
]
