"""Bounded SearXNG implementation of the provider-neutral web.search contract."""
from __future__ import annotations

from collections.abc import Mapping
import ipaddress
from typing import Any
from urllib.parse import urlsplit

import httpx


WEB_SEARCH = "web.search"
_MAX_URL_LENGTH = 4096


class SearXNGSearchProvider:
    """Bounded ``web.search`` provider backed by SearXNG's JSON endpoint."""

    provider_name = "searxng"
    contract_version = "runtime-web-search:v1"

    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:8080",
        timeout_seconds: float = 15.0,
        allow_insecure_http: bool = False,
        max_results: int = 20,
        max_query_chars: int = 512,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = _validate_service_url(
            base_url, allow_insecure_http=allow_insecure_http
        )
        self.max_results = _bounded_int(
            max_results, low=1, high=50, field="max_results"
        )
        self.max_query_chars = _bounded_int(
            max_query_chars, low=1, high=4096, field="max_query_chars"
        )
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=self.base_url,
            headers={"Accept": "application/json"},
            timeout=float(timeout_seconds),
        )

    def manifest(self) -> dict[str, Any]:
        return {
            "provider": self.provider_name,
            "version": self.contract_version,
            "capabilities": [
                {
                    "provider": self.provider_name,
                    "version": self.contract_version,
                    "capability_id": WEB_SEARCH,
                    "side_effect_level": "READ_ONLY",
                    "legacy_aliases": [],
                    "description": (
                        "Search the public web through a bounded provider-neutral "
                        "query contract."
                    ),
                }
            ],
        }

    def invoke(
        self,
        capability_id: str,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if capability_id != WEB_SEARCH:
            raise ValueError(f"unsupported web capability: {capability_id}")
        request = dict(payload or {})
        query = request.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")
        query = query.strip()
        if len(query) > self.max_query_chars:
            raise ValueError("query exceeds configured maximum length")
        limit = _bounded_int(
            request.get("limit", 10), low=1, high=self.max_results, field="limit"
        )
        page = _bounded_int(
            request.get("page", 1), low=1, high=10, field="page"
        )
        safesearch = _bounded_int(
            request.get("safesearch", 1), low=0, high=2, field="safesearch"
        )
        params: dict[str, str | int] = {
            "q": query,
            "format": "json",
            "pageno": page,
            "safesearch": safesearch,
        }
        language = request.get("language")
        if language is not None:
            if (
                not isinstance(language, str)
                or not language.strip()
                or len(language) > 32
            ):
                raise ValueError("language must be a short non-empty string")
            params["language"] = language.strip()
        categories = request.get("categories")
        if categories is not None:
            if (
                not isinstance(categories, list)
                or not categories
                or len(categories) > 8
                or any(
                    not isinstance(item, str) or not item.strip()
                    for item in categories
                )
            ):
                raise ValueError("categories must be 1-8 non-empty strings")
            params["categories"] = ",".join(
                item.strip() for item in categories
            )
        time_range = request.get("time_range")
        if time_range is not None:
            if time_range not in {"day", "month", "year"}:
                raise ValueError("time_range must be day, month, or year")
            params["time_range"] = time_range

        response = self._client.get("/search", params=params)
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, Mapping):
            raise TypeError("searxng response must be a mapping")
        raw_results = body.get("results", [])
        if not isinstance(raw_results, list):
            raise TypeError("searxng results must be a list")

        results: list[dict[str, Any]] = []
        for row in raw_results:
            if len(results) >= limit:
                break
            if not isinstance(row, Mapping):
                continue
            try:
                safe_url = _require_public_url(row.get("url"))
            except (TypeError, ValueError):
                continue
            results.append(
                {
                    "title": _bounded_text(row.get("title"), 1000),
                    "url": safe_url,
                    "snippet": _bounded_text(row.get("content"), 4000),
                    "engine": _bounded_text(row.get("engine"), 128),
                    "category": _bounded_text(row.get("category"), 128),
                    "score": (
                        row.get("score")
                        if isinstance(row.get("score"), (int, float))
                        and not isinstance(row.get("score"), bool)
                        else None
                    ),
                    "published_at": _bounded_text(
                        row.get("publishedDate") or row.get("published_date"), 128
                    ),
                }
            )
        return {
            "ok": True,
            "provider": self.provider_name,
            "capability_id": WEB_SEARCH,
            "query": query,
            "results": results,
            "result_count": len(results),
            "budget": {
                "limit": limit,
                "page": page,
                "max_query_chars": self.max_query_chars,
                "safesearch": safesearch,
            },
        }

    def health(self) -> dict[str, Any]:
        try:
            response = self._client.get(
                "/search",
                params={
                    "q": "runtime health probe",
                    "format": "json",
                    "pageno": 1,
                },
            )
            response.raise_for_status()
            payload = response.json()
            return {
                "ok": (
                    isinstance(payload, Mapping)
                    and isinstance(payload.get("results", []), list)
                ),
                "provider": self.provider_name,
            }
        except (httpx.HTTPError, TypeError, ValueError):
            return {"ok": False, "provider": self.provider_name}

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
    if lowered in {"localhost", "localhost.localdomain"} or lowered.endswith(
        (".local", ".internal")
    ):
        raise ValueError("local/private hostnames are not allowed")
    try:
        address = ipaddress.ip_address(lowered)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("non-global IP targets are not allowed")
    return url


def _validate_service_url(base_url: str, *, allow_insecure_http: bool) -> str:
    if not isinstance(base_url, str) or not base_url.strip():
        raise ValueError("base_url must be a non-empty string")
    value = base_url.rstrip("/")
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("base_url must be an http/https service URL")
    if parts.username is not None or parts.password is not None:
        raise ValueError("credentials must not be embedded in base_url")
    if parts.scheme == "http" and not allow_insecure_http:
        host = parts.hostname.rstrip(".").lower()
        if host not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError(
                "remote SearXNG service requires https or allow_insecure_http=True"
            )
    return value


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


__all__ = [
    "SearXNGSearchProvider",
    "WEB_SEARCH",
]
