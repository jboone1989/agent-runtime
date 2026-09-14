"""Bounded Jina Reader implementation of Agent Runtime's web.fetch contract."""
from __future__ import annotations

from collections.abc import Mapping
import ipaddress
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

WEB_FETCH = "web.fetch"
_MAX_URL_LENGTH = 4096
_DEFAULT_MAX_CHARS = 100_000
_HARD_MAX_CHARS = 200_000


class JinaReaderProvider:
    provider_name = "jina-reader"
    contract_version = "runtime-web-fetch:v1"

    def __init__(
        self,
        *,
        reader_base_url: str = "https://r.jina.ai",
        api_key: str | None = None,
        timeout_seconds: float = 40.0,
        max_chars: int = _DEFAULT_MAX_CHARS,
        allow_insecure_http: bool = False,
        client: httpx.Client | None = None,
    ) -> None:
        self.reader_base_url = _validate_service_url(
            reader_base_url, allow_insecure_http=allow_insecure_http
        )
        self.max_chars = _bounded_int(max_chars, low=1, high=_HARD_MAX_CHARS, field="max_chars")
        self._owns_client = client is None
        headers = {"Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._request_headers = headers
        self._client = client or httpx.Client(
            timeout=float(timeout_seconds), trust_env=False
        )

    def manifest(self) -> dict[str, Any]:
        return {
            "provider": self.provider_name,
            "version": self.contract_version,
            "capabilities": [{
                "provider": self.provider_name,
                "version": self.contract_version,
                "capability_id": WEB_FETCH,
                "side_effect_level": "READ_ONLY",
                "required_permissions": [],
                "legacy_aliases": [],
                "description": "Fetch one bounded public HTTP(S) page as LLM-friendly markdown.",
            }],
        }

    def invoke(self, capability_id: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if capability_id != WEB_FETCH:
            raise ValueError(f"unsupported web capability: {capability_id}")
        request = dict(payload or {})
        unknown = sorted(set(request) - {"url", "max_chars"})
        if unknown:
            raise ValueError(f"unknown web.fetch parameter: {unknown[0]}")
        target = _require_public_url(request.get("url"))
        max_chars = _bounded_int(
            request.get("max_chars", self.max_chars), low=1, high=self.max_chars, field="max_chars"
        )
        endpoint = self.reader_base_url + "/" + quote(target, safe=":/?&=%#[]@!$'()*+,;~")
        response = self._client.get(endpoint, headers=self._request_headers)
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, Mapping):
            raise TypeError("Jina Reader response must be a mapping")
        data = body.get("data")
        if not isinstance(data, Mapping):
            raise TypeError("Jina Reader response requires data mapping")
        returned_url = data.get("url") or target
        final_url = _require_public_url(returned_url)
        title = _bounded_text(data.get("title"), 1000)
        markdown = _bounded_text(data.get("content"), max_chars)
        if _looks_like_access_challenge(title, markdown):
            raise ValueError("Jina Reader returned an access-challenge page")
        success = bool(markdown.strip())
        return {
            "ok": success,
            "provider": self.provider_name,
            "capability_id": WEB_FETCH,
            "canonical_capability_id": WEB_FETCH,
            "page": {
                "url": final_url,
                "title": title,
                "markdown": markdown,
                "success": success,
            },
        }

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


def _require_public_url(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("url must be a non-empty string")
    url = value.strip()
    if len(url) > _MAX_URL_LENGTH:
        raise ValueError("url exceeds maximum length")
    parts = urlsplit(url)
    if parts.scheme.lower() not in {"http", "https"}:
        raise ValueError("only http/https URLs are allowed")
    if parts.username is not None or parts.password is not None:
        raise ValueError("credential-bearing URLs are not allowed")
    host = parts.hostname
    if not host:
        raise ValueError("url requires a hostname")
    lowered = host.rstrip(".").lower()
    if lowered in {"localhost", "localhost.localdomain"} or lowered.endswith((".local", ".internal")):
        raise ValueError("local/private hostnames are not allowed")
    try:
        address = ipaddress.ip_address(lowered)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("non-global IP targets are not allowed")
    return url


def _validate_service_url(value: str, *, allow_insecure_http: bool) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("reader_base_url must be a non-empty string")
    normalized = value.rstrip("/")
    parts = urlsplit(normalized)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("reader_base_url must be an http/https service URL")
    if parts.username is not None or parts.password is not None:
        raise ValueError("credentials must not be embedded in reader_base_url")
    if parts.scheme == "http" and not allow_insecure_http:
        host = parts.hostname.rstrip(".").lower()
        if host not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("remote Jina Reader service requires https or allow_insecure_http=True")
    return normalized


def _bounded_int(value: Any, *, low: int, high: int, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer") from exc
    if parsed < low or parsed > high:
        raise ValueError(f"{field} must be in [{low}, {high}]")
    return parsed


def _bounded_text(value: Any, max_chars: int) -> str:
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    return text[:max_chars]


def _looks_like_access_challenge(title: str, content: str) -> bool:
    sample = (title + "\n" + content[:1000]).lower()
    markers = (
        "just a moment...",
        "performing security verification",
        "checking your browser",
        "verify you are human",
    )
    return any(marker in sample for marker in markers)


__all__ = ["JinaReaderProvider", "WEB_FETCH"]
