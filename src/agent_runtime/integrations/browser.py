"""Deterministic bounded browser capabilities backed by Playwright."""
from __future__ import annotations

import base64
from collections.abc import Callable, Mapping
from dataclasses import dataclass
import ipaddress
import re
import socket
from typing import Any
from urllib.parse import urlsplit
import uuid

from ..capabilities.inventory import CapabilityInventory
from ..capabilities.model import (
    CapabilityAccessMode,
    CapabilityBinding,
    CapabilityKind,
    CapabilityLifecycleState,
    SideEffectLevel,
)


BROWSER_NAVIGATE = "browser.navigate"
BROWSER_INSPECT = "browser.inspect"
BROWSER_CLICK = "browser.click"
BROWSER_TYPE = "browser.type"
BROWSER_SCREENSHOT = "browser.screenshot"
_BROWSER_CAPABILITIES = (
    BROWSER_NAVIGATE,
    BROWSER_INSPECT,
    BROWSER_CLICK,
    BROWSER_TYPE,
    BROWSER_SCREENSHOT,
)
_SESSION_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


@dataclass(frozen=True)
class BrowserPolicy:
    max_sessions: int = 4
    timeout_seconds: float = 15.0
    max_text_chars: int = 100_000
    max_elements: int = 100
    max_input_chars: int = 10_000
    max_screenshot_bytes: int = 512_000
    allowed_hosts: frozenset[str] | None = None
    blocked_hosts: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not 1 <= int(self.max_sessions) <= 32:
            raise ValueError("max_sessions must be in [1, 32]")
        if not 0.1 <= float(self.timeout_seconds) <= 120:
            raise ValueError("timeout_seconds must be in [0.1, 120]")
        for value, name, ceiling in (
            (self.max_text_chars, "max_text_chars", 1_000_000),
            (self.max_elements, "max_elements", 500),
            (self.max_input_chars, "max_input_chars", 100_000),
            (self.max_screenshot_bytes, "max_screenshot_bytes", 2_000_000),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= ceiling:
                raise ValueError(f"{name} must be in [1, {ceiling}]")
        if self.allowed_hosts is not None:
            object.__setattr__(
                self,
                "allowed_hosts",
                frozenset(_normalize_host(item) for item in self.allowed_hosts),
            )
        object.__setattr__(
            self,
            "blocked_hosts",
            frozenset(_normalize_host(item) for item in self.blocked_hosts),
        )


@dataclass
class _Session:
    context: Any
    page: Any


class PlaywrightBrowserProvider:
    provider_name = "playwright"
    contract_version = "runtime-browser:v1"

    def __init__(
        self,
        *,
        policy: BrowserPolicy | None = None,
        browser_factory: Callable[[], Any] | None = None,
        dns_resolver: Callable[..., Any] = socket.getaddrinfo,
    ) -> None:
        self.policy = policy or BrowserPolicy()
        self._browser_factory = browser_factory
        self._dns_resolver = dns_resolver
        self._playwright = None
        self._browser = None
        self._sessions: dict[str, _Session] = {}
        self._closed = False
        self._session_epoch = f"browser-session:v1:{uuid.uuid4().hex}"

    def manifest(self) -> dict[str, Any]:
        rows = []
        for capability_id in _BROWSER_CAPABILITIES:
            interactive = capability_id in {BROWSER_CLICK, BROWSER_TYPE}
            rows.append(
                {
                    "capability_id": capability_id,
                    "provider": self.provider_name,
                    "version": self.contract_version,
                    "side_effect_level": (
                        "EXTERNAL_SIDE_EFFECT" if interactive else "READ_ONLY"
                    ),
                    "required_permissions": ["browser.interact"] if interactive else [],
                }
            )
        return {
            "provider": self.provider_name,
            "version": self.contract_version,
            "capabilities": rows,
        }

    def invoke(
        self, capability_id: str, payload: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        if self._closed:
            raise RuntimeError("browser provider is closed")
        request = dict(payload or {})
        if capability_id == BROWSER_NAVIGATE:
            return self._navigate(request)
        if capability_id == BROWSER_INSPECT:
            return self._inspect(request)
        if capability_id == BROWSER_CLICK:
            return self._click(request)
        if capability_id == BROWSER_TYPE:
            return self._type(request)
        if capability_id == BROWSER_SCREENSHOT:
            return self._screenshot(request)
        raise ValueError(f"unsupported browser capability: {capability_id}")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for session in list(self._sessions.values()):
            try:
                session.context.close()
            except Exception:
                pass
        self._sessions.clear()
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

    def _navigate(self, request: dict[str, Any]) -> dict[str, Any]:
        session_id = _session_id(request.get("session_id", "default"))
        url = self._validate_url(request.get("url"))
        session = self._session(session_id, create=True)
        response = session.page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=int(self.policy.timeout_seconds * 1000),
        )
        final_url = self._validate_url(str(session.page.url))
        return {
            "ok": True,
            "provider": self.provider_name,
            "capability_id": BROWSER_NAVIGATE,
            "session_id": session_id,
            "session_epoch": self._session_epoch,
            "restart_semantics": "browser_sessions_are_process_local",
            "url": final_url,
            "title": _bounded_text(session.page.title(), 1000),
            "status_code": getattr(response, "status", None),
        }

    def _inspect(self, request: dict[str, Any]) -> dict[str, Any]:
        session_id = _session_id(request.get("session_id", "default"))
        session = self._session(session_id)
        max_chars = _bounded_int(
            request.get("max_chars", self.policy.max_text_chars),
            1,
            self.policy.max_text_chars,
            "max_chars",
        )
        max_elements = _bounded_int(
            request.get("max_elements", self.policy.max_elements),
            1,
            self.policy.max_elements,
            "max_elements",
        )
        body = session.page.locator("body")
        text = str(body.inner_text(timeout=int(self.policy.timeout_seconds * 1000)))
        interactive = session.page.locator(
            "a,button,input,textarea,select,[role=button],[role=link]"
        )
        count = min(int(interactive.count()), max_elements)
        elements: list[dict[str, Any]] = []
        for index in range(count):
            item = interactive.nth(index)
            elements.append(
                {
                    "index": index,
                    "tag": _bounded_text(
                        item.evaluate("el => el.tagName.toLowerCase()"), 32
                    ),
                    "role": _bounded_text(item.get_attribute("role"), 64),
                    "name": _element_name(item),
                    "href": _bounded_text(item.get_attribute("href"), 2048),
                    "type": _bounded_text(item.get_attribute("type"), 64),
                }
            )
        bounded = text[:max_chars]
        return {
            "ok": True,
            "provider": self.provider_name,
            "capability_id": BROWSER_INSPECT,
            "session_id": session_id,
            "url": self._validate_url(str(session.page.url)),
            "title": _bounded_text(session.page.title(), 1000),
            "text": bounded,
            "text_truncated": len(bounded) < len(text),
            "text_original_chars": len(text),
            "elements": elements,
            "elements_truncated": int(interactive.count()) > max_elements,
        }

    def _click(self, request: dict[str, Any]) -> dict[str, Any]:
        session_id = _session_id(request.get("session_id", "default"))
        selector = _selector(request.get("selector"))
        session = self._session(session_id)
        session.page.locator(selector).click(timeout=int(self.policy.timeout_seconds * 1000))
        return {
            "ok": True,
            "provider": self.provider_name,
            "capability_id": BROWSER_CLICK,
            "session_id": session_id,
            "url": self._validate_url(str(session.page.url)),
            "selector": selector,
        }

    def _type(self, request: dict[str, Any]) -> dict[str, Any]:
        session_id = _session_id(request.get("session_id", "default"))
        selector = _selector(request.get("selector"))
        text = request.get("text")
        if not isinstance(text, str) or len(text) > self.policy.max_input_chars:
            raise ValueError("browser input text exceeds the configured bound")
        session = self._session(session_id)
        session.page.locator(selector).fill(
            text, timeout=int(self.policy.timeout_seconds * 1000)
        )
        return {
            "ok": True,
            "provider": self.provider_name,
            "capability_id": BROWSER_TYPE,
            "session_id": session_id,
            "url": self._validate_url(str(session.page.url)),
            "selector": selector,
            "chars_written": len(text),
        }

    def _screenshot(self, request: dict[str, Any]) -> dict[str, Any]:
        session_id = _session_id(request.get("session_id", "default"))
        full_page = request.get("full_page", False)
        if not isinstance(full_page, bool):
            raise ValueError("full_page must be boolean")
        session = self._session(session_id)
        raw = session.page.screenshot(type="png", full_page=full_page)
        if not isinstance(raw, (bytes, bytearray)):
            raise TypeError("browser screenshot must return bytes")
        if len(raw) > self.policy.max_screenshot_bytes:
            raise ValueError("browser screenshot exceeds configured byte limit")
        return {
            "ok": True,
            "provider": self.provider_name,
            "capability_id": BROWSER_SCREENSHOT,
            "session_id": session_id,
            "url": self._validate_url(str(session.page.url)),
            "encoding": "base64",
            "media_type": "image/png",
            "bytes": len(raw),
            "content": base64.b64encode(bytes(raw)).decode("ascii"),
        }

    def _session(self, session_id: str, *, create: bool = False) -> _Session:
        existing = self._sessions.get(session_id)
        if existing is not None:
            return existing
        if not create:
            raise LookupError(f"browser session does not exist: {session_id}")
        if len(self._sessions) >= self.policy.max_sessions:
            raise RuntimeError("browser session limit exceeded")
        browser = self._ensure_browser()
        context = browser.new_context(accept_downloads=False)
        if hasattr(context, "route"):
            context.route("**/*", self._route_request)
        page = context.new_page()
        session = _Session(context=context, page=page)
        self._sessions[session_id] = session
        return session

    def _ensure_browser(self) -> Any:
        if self._browser is not None:
            return self._browser
        if self._browser_factory is not None:
            self._browser = self._browser_factory()
            return self._browser
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "browser extra missing; install agent-runtime[browser]"
            ) from exc
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=True)
        return self._browser

    def _route_request(self, route: Any, request: Any) -> None:
        try:
            self._validate_url(str(request.url))
        except Exception:
            route.abort()
            return
        route.continue_()

    def _validate_url(self, value: Any) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > 4096:
            raise ValueError("browser url must be a bounded non-empty string")
        url = value.strip()
        parsed = urlsplit(url)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            raise ValueError("browser supports only http/https URLs")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("credential-bearing browser URLs are not allowed")
        host = parsed.hostname.rstrip(".").lower()
        if host in {"localhost", "localhost.localdomain"} or host.endswith(
            (".local", ".internal")
        ):
            raise ValueError("local/private browser hosts are not allowed")
        if any(_host_matches(host, item) for item in self.policy.blocked_hosts):
            raise ValueError("browser host is blocked by policy")
        if self.policy.allowed_hosts is not None and not any(
            _host_matches(host, item) for item in self.policy.allowed_hosts
        ):
            raise ValueError("browser host is outside the configured allowlist")
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        if literal is not None:
            if not literal.is_global:
                raise ValueError("non-global browser IP targets are not allowed")
        else:
            self._validate_dns(host, parsed.port or (443 if parsed.scheme == "https" else 80))
        return url

    def _validate_dns(self, host: str, port: int) -> None:
        try:
            addresses = self._dns_resolver(host, port, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise ValueError("browser host could not be resolved") from exc
        if not addresses:
            raise ValueError("browser host could not be resolved")
        for row in addresses:
            try:
                address = ipaddress.ip_address(row[4][0])
            except (IndexError, TypeError, ValueError):
                raise ValueError("browser DNS result is invalid")
            if not address.is_global:
                raise ValueError("browser DNS resolved to a non-global address")


def register_browser_provider(
    provider: PlaywrightBrowserProvider, inventory: CapabilityInventory
) -> tuple[str, ...]:
    registered: list[str] = []
    for row in provider.manifest()["capabilities"]:
        capability_id = str(row["capability_id"])
        if inventory.get(capability_id) is not None:
            raise ValueError(f"browser capability already registered: {capability_id}")
        level = SideEffectLevel[str(row["side_effect_level"])]
        inventory.register(
            CapabilityBinding(
                capability_id=capability_id,
                kind=CapabilityKind.SERVICE,
                provider=provider.provider_name,
                candidate_id=f"{provider.provider_name}:{capability_id}",
                access_mode=CapabilityAccessMode.LOCAL,
                state=CapabilityLifecycleState.ACTIVE,
                version=provider.contract_version,
                side_effects=level,
                entry_point={
                    "adapter": "PlaywrightBrowserProvider",
                    "required_permissions": list(row.get("required_permissions") or ()),
                },
                evidence_refs=["contract:runtime-browser:v1"],
            )
        )
        registered.append(capability_id)
    return tuple(registered)


def _session_id(value: Any) -> str:
    if not isinstance(value, str) or not _SESSION_RE.fullmatch(value):
        raise ValueError("session_id must match [A-Za-z0-9._-]{1,64}")
    return value


def _selector(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 1024:
        raise ValueError("selector must be a bounded non-empty string")
    return value.strip()


def _bounded_int(value: Any, low: int, high: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{field} must be in [{low}, {high}]")
    return value


def _bounded_text(value: Any, max_chars: int) -> str:
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    return text[:max_chars]


def _element_name(item: Any) -> str:
    for getter in (
        lambda: item.get_attribute("aria-label"),
        lambda: item.get_attribute("placeholder"),
        lambda: item.get_attribute("name"),
        lambda: item.inner_text(timeout=1000),
    ):
        try:
            value = getter()
        except Exception:
            continue
        if isinstance(value, str) and value.strip():
            return value.strip()[:500]
    return ""


def _normalize_host(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("browser host policy entries must be non-empty strings")
    host = value.strip().rstrip(".").lower()
    if "://" in host or "/" in host:
        raise ValueError("browser host policy entries must be hostnames")
    return host


def _host_matches(host: str, pattern: str) -> bool:
    return host == pattern or host.endswith("." + pattern)


__all__ = [
    "BROWSER_CLICK",
    "BROWSER_INSPECT",
    "BROWSER_NAVIGATE",
    "BROWSER_SCREENSHOT",
    "BROWSER_TYPE",
    "BrowserPolicy",
    "PlaywrightBrowserProvider",
    "register_browser_provider",
]
