"""Bounded web observation capabilities backed by a Crawl4AI HTTP service.

Runtime owns the stable capability contract. Crawl4AI is an implementation
provider, not a dependency of the Runtime core. The provider intentionally
submits only URL seeds to Crawl4AI; Runtime performs bounded traversal itself
instead of exposing request-supplied browser code, hooks, proxies, or deep-crawl
objects across the service trust boundary.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
import ipaddress
import time
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from ..capabilities.inventory import CapabilityInventory
from ..capabilities.model import (
    CapabilityAccessMode,
    CapabilityBinding,
    CapabilityKind,
    CapabilityLifecycleState,
    SideEffectLevel,
)

WEB_FETCH = "web.fetch"
WEB_CRAWL = "web.crawl"

_MAX_URL_LENGTH = 4096
_MAX_PAGES_HARD = 25
_MAX_DEPTH_HARD = 5
_DEFAULT_MAX_CHARS = 200_000


@dataclass(frozen=True)
class WebFetchPolicy:
    """Local safety and resource policy for one web provider."""

    max_pages: int = _MAX_PAGES_HARD
    max_depth: int = _MAX_DEPTH_HARD
    max_chars_per_page: int = _DEFAULT_MAX_CHARS
    task_poll_seconds: float = 0.25
    task_poll_attempts: int = 120
    allowed_hosts: frozenset[str] | None = None
    blocked_hosts: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not 1 <= int(self.max_pages) <= _MAX_PAGES_HARD:
            raise ValueError(f"max_pages must be in [1, {_MAX_PAGES_HARD}]")
        if not 0 <= int(self.max_depth) <= _MAX_DEPTH_HARD:
            raise ValueError(f"max_depth must be in [0, {_MAX_DEPTH_HARD}]")
        if int(self.max_chars_per_page) < 1:
            raise ValueError("max_chars_per_page must be positive")
        if float(self.task_poll_seconds) < 0:
            raise ValueError("task_poll_seconds must be non-negative")
        if int(self.task_poll_attempts) < 1:
            raise ValueError("task_poll_attempts must be positive")
        if self.allowed_hosts is not None:
            object.__setattr__(
                self,
                "allowed_hosts",
                frozenset(_normalize_host_pattern(item) for item in self.allowed_hosts),
            )
        object.__setattr__(
            self,
            "blocked_hosts",
            frozenset(_normalize_host_pattern(item) for item in self.blocked_hosts),
        )


class Crawl4AIHTTPProvider:
    """Runtime capability provider over Crawl4AI's secure HTTP API.

    Target Crawl4AI v0.9+ for the secure-by-default network API. The Runtime
    layer adds a second safety boundary: public HTTP(S) targets only, optional
    host allow/block lists, bounded depth/pages/content, and no caller-supplied
    executable browser configuration.
    """

    provider_name = "crawl4ai"
    contract_version = "runtime-web:v1"

    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:11235",
        api_token: str | None = None,
        timeout_seconds: float = 60.0,
        allow_insecure_http: bool = False,
        policy: WebFetchPolicy | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = _validate_service_url(
            base_url, allow_insecure_http=allow_insecure_http
        )
        self.policy = policy or WebFetchPolicy()
        self._owns_client = client is None
        headers = {"Accept": "application/json"}
        if api_token:
            headers["Authorization"] = f"Bearer {api_token}"
        self._client = client or httpx.Client(
            base_url=self.base_url,
            headers=headers,
            timeout=float(timeout_seconds),
        )

    def manifest(self) -> dict[str, Any]:
        common = {
            "provider": self.provider_name,
            "version": self.contract_version,
            "side_effect_level": "READ_ONLY",
        }
        return {
            "provider": self.provider_name,
            "version": self.contract_version,
            "capabilities": [
                {
                    **common,
                    "capability_id": WEB_FETCH,
                    "legacy_aliases": [],
                    "description": "Fetch one bounded public HTTP(S) page as LLM-friendly content.",
                },
                {
                    **common,
                    "capability_id": WEB_CRAWL,
                    "legacy_aliases": [],
                    "description": "Bounded breadth-first crawl over public HTTP(S) pages.",
                },
            ],
        }

    def health(self) -> dict[str, Any]:
        try:
            response = self._client.get("/health")
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, Mapping):
                return {"ok": False, "reason": "health_response_not_mapping"}
            status = str(payload.get("status", "ok")).lower()
            return {
                "ok": status not in {"error", "failed", "unhealthy", "degraded"},
                "provider": self.provider_name,
                "status": status,
                "details": dict(payload),
            }
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            return {
                "ok": False,
                "provider": self.provider_name,
                "reason": f"health_error:{type(exc).__name__}",
            }

    def invoke(
        self,
        capability_id: str,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        request = dict(payload or {})
        if capability_id == WEB_FETCH:
            return self._fetch_capability(request, requested_capability=capability_id)
        if capability_id == WEB_CRAWL:
            return self._crawl_capability(request)
        raise ValueError(f"unsupported web capability: {capability_id}")

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _target_url(self, value: Any) -> str:
        url = _require_public_url(value)
        host = (urlsplit(url).hostname or "").rstrip(".").lower()
        if any(_host_matches(host, pattern) for pattern in self.policy.blocked_hosts):
            raise ValueError("target host is blocked by web policy")
        if self.policy.allowed_hosts is not None and not any(
            _host_matches(host, pattern) for pattern in self.policy.allowed_hosts
        ):
            raise ValueError("target host is outside the web allowlist")
        return url

    def _fetch_capability(
        self,
        request: dict[str, Any],
        *,
        requested_capability: str,
    ) -> dict[str, Any]:
        url = self._target_url(request.get("url"))
        max_chars = _bounded_int(
            request.get("max_chars", self.policy.max_chars_per_page),
            low=1,
            high=self.policy.max_chars_per_page,
            field="max_chars",
        )
        include_html = bool(request.get("include_html", False))
        page = self._fetch_page(url, max_chars=max_chars, include_html=include_html)
        return {
            "ok": bool(page.get("success")),
            "provider": self.provider_name,
            "capability_id": requested_capability,
            "canonical_capability_id": WEB_FETCH,
            "page": page,
        }

    def _crawl_capability(self, request: dict[str, Any]) -> dict[str, Any]:
        seed = self._target_url(request.get("url"))
        max_depth = _bounded_int(
            request.get("max_depth", 1),
            low=0,
            high=self.policy.max_depth,
            field="max_depth",
        )
        max_pages = _bounded_int(
            request.get("max_pages", 10),
            low=1,
            high=self.policy.max_pages,
            field="max_pages",
        )
        max_chars = _bounded_int(
            request.get("max_chars", self.policy.max_chars_per_page),
            low=1,
            high=self.policy.max_chars_per_page,
            field="max_chars",
        )
        include_external = bool(request.get("include_external", False))

        root_host = (urlsplit(seed).hostname or "").rstrip(".").lower()
        queue: deque[tuple[str, int]] = deque([(seed, 0)])
        seen: set[str] = set()
        queued: set[str] = {_canonical_url(seed)}
        pages: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []

        while queue and len(pages) < max_pages:
            current, depth = queue.popleft()
            canonical = _canonical_url(current)
            if canonical in seen:
                continue
            seen.add(canonical)
            try:
                page = self._fetch_page(
                    canonical,
                    max_chars=max_chars,
                    include_html=False,
                )
            except Exception as exc:
                errors.append({"url": canonical, "error": type(exc).__name__})
                continue
            page["depth"] = depth
            pages.append(page)
            if depth >= max_depth:
                continue
            for raw_link in page.get("links", []):
                candidate = _safe_join(canonical, raw_link)
                if candidate is None:
                    continue
                try:
                    candidate = self._target_url(candidate)
                except ValueError:
                    continue
                candidate = _canonical_url(candidate)
                if candidate in seen or candidate in queued:
                    continue
                candidate_host = (urlsplit(candidate).hostname or "").rstrip(".").lower()
                if not include_external and candidate_host != root_host:
                    continue
                queued.add(candidate)
                queue.append((candidate, depth + 1))

        successful = sum(1 for page in pages if page.get("success"))
        return {
            "ok": successful > 0,
            "provider": self.provider_name,
            "capability_id": WEB_CRAWL,
            "seed_url": seed,
            "pages": pages,
            "page_count": len(pages),
            "successful_pages": successful,
            "errors": errors,
            "budget": {
                "max_depth": max_depth,
                "max_pages": max_pages,
                "max_chars_per_page": max_chars,
                "include_external": include_external,
            },
        }

    def _fetch_page(
        self,
        url: str,
        *,
        max_chars: int,
        include_html: bool,
    ) -> dict[str, Any]:
        response = self._client.post("/crawl", json={"urls": [url]})
        response.raise_for_status()
        payload = response.json()
        results = self._crawl_results(payload)
        if not results:
            raise ValueError("crawl4ai returned no results")
        row = results[0]
        if not isinstance(row, Mapping):
            raise TypeError("crawl4ai result must be a mapping")
        return _normalize_page(
            dict(row),
            requested_url=url,
            max_chars=max_chars,
            include_html=include_html,
        )

    def _crawl_results(self, payload: Any) -> list[Any]:
        if isinstance(payload, list):
            return payload
        if not isinstance(payload, Mapping):
            raise TypeError("crawl4ai response must be a mapping or list")
        results = payload.get("results")
        if isinstance(results, list):
            return results
        if isinstance(results, Mapping):
            return [dict(results)]
        result = payload.get("result")
        if isinstance(result, list):
            return result
        if isinstance(result, Mapping):
            return [dict(result)]
        task_id = payload.get("task_id")
        if isinstance(task_id, str) and task_id:
            return self._poll_task(task_id)
        return []

    def _poll_task(self, task_id: str) -> list[Any]:
        for attempt in range(self.policy.task_poll_attempts):
            if attempt and self.policy.task_poll_seconds:
                time.sleep(self.policy.task_poll_seconds)
            response = self._client.get(f"/task/{task_id}")
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, Mapping):
                raise TypeError("crawl4ai task response must be a mapping")
            status = str(payload.get("status", "")).lower()
            if status in {"failed", "error", "cancelled", "canceled"}:
                raise RuntimeError(f"crawl4ai task ended with status={status}")
            results = payload.get("results")
            if isinstance(results, list):
                return results
            result = payload.get("result")
            if isinstance(result, list):
                return result
            if isinstance(result, Mapping):
                return [dict(result)]
            if status in {"completed", "complete", "done", "success"}:
                return []
        raise TimeoutError("crawl4ai task did not complete within polling budget")


class WebCapabilityAdapter:
    """Register a web provider in the existing CapabilityInventory path."""

    def __init__(self, provider: Any) -> None:
        self.provider = provider
        self._manifest: dict[str, Any] | None = None
        self._aliases: dict[str, str] = {}

    def manifest(self) -> dict[str, Any]:
        if self._manifest is None:
            raw = self.provider.manifest()
            if not isinstance(raw, Mapping):
                raise TypeError("web provider manifest must be a mapping")
            self._manifest = dict(raw)
        return dict(self._manifest)

    def register(self, inventory: CapabilityInventory) -> tuple[str, ...]:
        manifest = self.manifest()
        provider_name = _required_string(manifest, "provider")
        provider_version = _required_string(manifest, "version")
        rows = manifest.get("capabilities")
        if not isinstance(rows, list):
            raise ValueError("web provider manifest capabilities must be a list")

        prepared: list[tuple[str, str, SideEffectLevel, tuple[str, ...]]] = []
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, Mapping):
                raise TypeError("web capability manifest rows must be mappings")
            capability_id = _required_string(row, "capability_id")
            if row.get("provider") != provider_name:
                raise ValueError(f"web capability provider mismatch: {capability_id}")
            version = _required_string(row, "version")
            side_effects = _side_effect_level(row.get("side_effect_level"))
            if side_effects.value > SideEffectLevel.READ_ONLY.value:
                raise ValueError(
                    "web observation provider may not register write-side capabilities"
                )
            aliases = _aliases(row.get("legacy_aliases"), canonical_id=capability_id)
            for registered_id in (capability_id, *aliases):
                if registered_id in seen:
                    raise ValueError(f"web capability ID collision: {registered_id}")
                if inventory.get(registered_id) is not None:
                    raise ValueError(
                        f"web capability already registered: {registered_id}"
                    )
                seen.add(registered_id)
            prepared.append((capability_id, version, side_effects, aliases))

        registered: list[str] = []
        for capability_id, version, side_effects, aliases in prepared:
            for registered_id in (capability_id, *aliases):
                inventory.register(
                    CapabilityBinding(
                        capability_id=registered_id,
                        kind=CapabilityKind.SERVICE,
                        provider=provider_name,
                        candidate_id=f"{provider_name}:{capability_id}",
                        access_mode=CapabilityAccessMode.LOCAL,
                        state=CapabilityLifecycleState.ACTIVE,
                        version=version or provider_version,
                        side_effects=side_effects,
                        entry_point={
                            "adapter": "WebCapabilityAdapter",
                            "capability_id": capability_id,
                            "provider": provider_name,
                            "legacy_aliases": list(aliases),
                        },
                        evidence_refs=[
                            f"manifest:{provider_name}",
                            "contract:runtime-web:v1",
                        ],
                    )
                )
                registered.append(registered_id)
                if registered_id != capability_id:
                    self._aliases[registered_id] = capability_id
        return tuple(registered)

    def invoke(
        self,
        capability_id: str,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        canonical = self._aliases.get(capability_id, capability_id)
        result = self.provider.invoke(canonical, payload or {})
        if not isinstance(result, Mapping):
            raise TypeError("web provider invocation must return a mapping")
        return dict(result)

    def health(self) -> dict[str, Any]:
        result = self.provider.health()
        if not isinstance(result, Mapping):
            raise TypeError("web provider health must return a mapping")
        return dict(result)

    def close(self) -> None:
        close = getattr(self.provider, "close", None)
        if callable(close):
            close()


def register_web_provider(
    provider: Any,
    inventory: CapabilityInventory,
) -> WebCapabilityAdapter:
    adapter = WebCapabilityAdapter(provider)
    adapter.register(inventory)
    return adapter


def register_crawl4ai_provider(
    inventory: CapabilityInventory,
    **provider_kwargs: Any,
) -> WebCapabilityAdapter:
    provider = Crawl4AIHTTPProvider(**provider_kwargs)
    return register_web_provider(provider, inventory)


def _normalize_page(
    row: dict[str, Any],
    *,
    requested_url: str,
    max_chars: int,
    include_html: bool,
) -> dict[str, Any]:
    markdown = _extract_markdown(row.get("markdown"))
    if not markdown and isinstance(row.get("fit_markdown"), str):
        markdown = row["fit_markdown"]
    if not markdown and isinstance(row.get("cleaned_text"), str):
        markdown = row["cleaned_text"]
    markdown, markdown_truncated, markdown_original_chars = _truncate(
        markdown, max_chars
    )

    output: dict[str, Any] = {
        "url": str(row.get("url") or requested_url),
        "success": bool(row.get("success", True)),
        "status_code": row.get("status_code"),
        "markdown": markdown,
        "markdown_truncated": markdown_truncated,
        "markdown_original_chars": markdown_original_chars,
        "links": _extract_links(row.get("links")),
        "metadata": (
            dict(row.get("metadata"))
            if isinstance(row.get("metadata"), Mapping)
            else {}
        ),
        "error": row.get("error_message") or row.get("error"),
    }
    if include_html:
        html = row.get("cleaned_html") or row.get("html") or ""
        html_text = html if isinstance(html, str) else str(html)
        bounded, truncated, original_chars = _truncate(html_text, max_chars)
        output.update(
            {
                "html": bounded,
                "html_truncated": truncated,
                "html_original_chars": original_chars,
            }
        )
    return output


def _extract_markdown(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        for key in (
            "fit_markdown",
            "raw_markdown",
            "markdown_with_citations",
            "text",
        ):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate:
                return candidate
    return ""


def _extract_links(value: Any) -> list[str]:
    found: list[str] = []

    def visit(item: Any) -> None:
        if isinstance(item, str):
            found.append(item)
        elif isinstance(item, Mapping):
            href = item.get("href") or item.get("url")
            if isinstance(href, str):
                found.append(href)
            else:
                for child in item.values():
                    visit(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                visit(child)

    visit(value)
    return list(dict.fromkeys(found))


def _safe_join(base: str, raw_link: Any) -> str | None:
    if not isinstance(raw_link, str) or not raw_link.strip():
        return None
    try:
        return _require_public_url(urljoin(base, raw_link.strip()))
    except (TypeError, ValueError):
        return None


def _canonical_url(url: str) -> str:
    parts = urlsplit(_require_public_url(url))
    scheme = parts.scheme.lower()
    hostname = (parts.hostname or "").lower()
    port = parts.port
    rendered_host = f"[{hostname}]" if ":" in hostname else hostname
    if port is None or (scheme == "http" and port == 80) or (
        scheme == "https" and port == 443
    ):
        netloc = rendered_host
    else:
        netloc = f"{rendered_host}:{port}"
    path = parts.path or "/"
    return urlunsplit((scheme, netloc, path, parts.query, ""))


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


def _normalize_host_pattern(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("host policy entries must be non-empty strings")
    host = value.strip().rstrip(".").lower()
    if "://" in host or "/" in host:
        raise ValueError("host policy entries must be hostnames, not URLs")
    return host


def _host_matches(host: str, pattern: str) -> bool:
    return host == pattern or host.endswith("." + pattern)


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
        loopback = host in {"localhost", "127.0.0.1", "::1"}
        if not loopback:
            raise ValueError(
                "remote Crawl4AI service requires https or allow_insecure_http=True"
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


def _truncate(value: str, max_chars: int) -> tuple[str, bool, int]:
    original = len(value)
    if original <= max_chars:
        return value, False, original
    return value[:max_chars], True, original


def _required_string(row: Mapping[str, Any], field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"web capability manifest requires non-empty {field}")
    return value


def _side_effect_level(value: Any) -> SideEffectLevel:
    if not isinstance(value, str):
        raise ValueError("web capability manifest requires side_effect_level")
    try:
        return SideEffectLevel[value]
    except KeyError as exc:
        raise ValueError(f"unknown web side effect level: {value}") from exc


def _aliases(value: Any, *, canonical_id: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise ValueError(
            "web capability legacy_aliases must be a list of non-empty strings"
        )
    if len(set(value)) != len(value):
        raise ValueError("web capability legacy_aliases must be unique")
    if canonical_id in value:
        raise ValueError(
            f"web capability alias duplicates canonical ID: {canonical_id}"
        )
    return tuple(value)
