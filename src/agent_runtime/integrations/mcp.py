"""Policy-first Model Context Protocol integration.

MCP discovery is evidence, not authorization. Runtime exposes only tools that
are explicitly mapped to provider-neutral capability IDs with Runtime-owned
side-effect and permission classifications.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
import re
import threading
from typing import Any
from urllib.parse import urlsplit

from ..capabilities.inventory import CapabilityInventory
from ..capabilities.model import (
    CapabilityAccessMode,
    CapabilityBinding,
    CapabilityCandidate,
    CapabilityKind,
    CapabilityLifecycleState,
    SideEffectLevel,
)


@dataclass(frozen=True)
class MCPToolBinding:
    """Runtime-owned classification for one MCP tool."""

    capability_id: str
    tool_name: str
    side_effect_level: SideEffectLevel
    required_permissions: frozenset[str] = frozenset()
    description: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.capability_id, str) or not self.capability_id.strip():
            raise ValueError("MCP capability_id must be non-empty")
        if not isinstance(self.tool_name, str) or not self.tool_name.strip():
            raise ValueError("MCP tool_name must be non-empty")
        if any(not isinstance(item, str) or not item.strip() for item in self.required_permissions):
            raise ValueError("MCP required_permissions must contain non-empty strings")


class MCPProvider:
    """Expose explicitly approved MCP tools through Runtime contracts."""

    contract_version = "runtime-mcp:v1"

    def __init__(
        self,
        *,
        server_id: str,
        target: Any,
        bindings: tuple[MCPToolBinding, ...],
        client_factory: Callable[[Any], Any] | None = None,
        timeout_seconds: float = 30.0,
        max_content_chars: int = 100_000,
        allow_insecure_http: bool = False,
    ) -> None:
        if not isinstance(server_id, str) or not server_id.strip():
            raise ValueError("server_id must be non-empty")
        if not bindings:
            raise ValueError("at least one approved MCP tool binding is required")
        capability_ids = [item.capability_id for item in bindings]
        tool_names = [item.tool_name for item in bindings]
        if len(capability_ids) != len(set(capability_ids)):
            raise ValueError("MCP capability IDs must be unique")
        if len(tool_names) != len(set(tool_names)):
            raise ValueError("MCP tool names must be unique within one provider")
        if timeout_seconds <= 0 or max_content_chars <= 0:
            raise ValueError("MCP timeout and content bounds must be positive")

        self.server_id = server_id.strip()
        self.provider_name = f"mcp:{self.server_id}"
        self.target = _validate_target(target, allow_insecure_http=allow_insecure_http)
        self.bindings = tuple(bindings)
        self._by_capability = {item.capability_id: item for item in bindings}
        self._client_factory = client_factory
        self.timeout_seconds = float(timeout_seconds)
        self.max_content_chars = int(max_content_chars)

    def manifest(self) -> dict[str, Any]:
        return {
            "provider": self.provider_name,
            "version": self.contract_version,
            "capabilities": [
                {
                    "capability_id": item.capability_id,
                    "provider": self.provider_name,
                    "version": self.contract_version,
                    "side_effect_level": item.side_effect_level.name,
                    "required_permissions": sorted(item.required_permissions),
                    "description": item.description or f"Approved MCP tool {item.tool_name}",
                    "mcp_tool": item.tool_name,
                }
                for item in self.bindings
            ],
        }

    def discover_candidates(self) -> tuple[CapabilityCandidate, ...]:
        """Discover server claims without promoting them to executable bindings."""
        tools = _run_async(self._list_tools(), timeout=self.timeout_seconds)
        if len(tools) > 500:
            raise RuntimeError("MCP discovery exceeds tool count bound")
        approved = {item.tool_name: item for item in self.bindings}
        candidates: list[CapabilityCandidate] = []
        for tool in tools:
            name = str(getattr(tool, "name", "") or "")
            if not name:
                continue
            mapped = approved.get(name)
            input_schema = getattr(tool, "input_schema", None)
            if input_schema is None:
                input_schema = getattr(tool, "inputSchema", None)
            candidates.append(
                CapabilityCandidate(
                    candidate_id=f"{self.provider_name}:{name}",
                    capability_id=(
                        mapped.capability_id
                        if mapped is not None
                        else f"mcp.tool.{_slug(self.server_id)}.{_slug(name)}"
                    ),
                    kind=CapabilityKind.MCP,
                    provider=self.provider_name,
                    description=str(getattr(tool, "description", "") or ""),
                    version=self.contract_version,
                    input_schema=(
                        dict(input_schema) if isinstance(input_schema, Mapping) else None
                    ),
                    permissions=(
                        set(mapped.required_permissions)
                        if mapped is not None
                        else {"mcp.unclassified"}
                    ),
                    side_effects=(
                        mapped.side_effect_level
                        if mapped is not None
                        else SideEffectLevel.IRREVERSIBLE
                    ),
                    acquisition_mode="MCP",
                    source=self.server_id,
                    provenance={
                        "server_id": self.server_id,
                        "tool_name": name,
                        "server_claim_only": mapped is None,
                    },
                    evidence_refs=[f"mcp-discovery:{self.server_id}:{name}"],
                )
            )
        return tuple(candidates)

    def verify_bindings(self) -> tuple[str, ...]:
        advertised = {
            candidate.provenance["tool_name"]
            for candidate in self.discover_candidates()
            if isinstance(candidate.provenance.get("tool_name"), str)
        }
        missing = sorted(
            item.tool_name for item in self.bindings if item.tool_name not in advertised
        )
        if missing:
            raise ValueError(
                "approved MCP tools are not advertised by server: " + ",".join(missing)
            )
        return tuple(item.tool_name for item in self.bindings)

    def invoke(
        self,
        capability_id: str,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        binding = self._by_capability.get(capability_id)
        if binding is None:
            raise ValueError(f"unapproved MCP capability: {capability_id}")
        result = _run_async(
            self._call_tool(binding.tool_name, dict(payload or {})),
            timeout=self.timeout_seconds,
        )
        return {
            "ok": not bool(getattr(result, "is_error", False)),
            "provider": self.provider_name,
            "capability_id": capability_id,
            "tool_name": binding.tool_name,
            "structured_content": _bounded_json(
                getattr(result, "structured_content", None), self.max_content_chars
            ),
            "content": _normalize_content(
                getattr(result, "content", ()), self.max_content_chars
            ),
        }

    async def _list_tools(self) -> list[Any]:
        async with self._client() as client:
            result = await client.list_tools()
            tools = getattr(result, "tools", ())
            return list(tools or ())

    async def _call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        async with self._client() as client:
            return await client.call_tool(tool_name, arguments)

    def _client(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory(self.target)
        try:
            from mcp import Client
        except ImportError as exc:
            raise RuntimeError("MCP extra missing; install agent-runtime[mcp]") from exc
        return Client(self.target)


class MCPProviderAdapter:
    """Register approved MCP mappings in the existing CapabilityInventory."""

    def __init__(self, provider: MCPProvider) -> None:
        self.provider = provider

    def register(self, inventory: CapabilityInventory) -> tuple[str, ...]:
        self.provider.verify_bindings()
        for item in self.provider.bindings:
            if inventory.get(item.capability_id) is not None:
                raise ValueError(f"MCP capability already registered: {item.capability_id}")
        for item in self.provider.bindings:
            inventory.register(
                CapabilityBinding(
                    capability_id=item.capability_id,
                    kind=CapabilityKind.MCP,
                    provider=self.provider.provider_name,
                    candidate_id=f"{self.provider.provider_name}:{item.tool_name}",
                    access_mode=CapabilityAccessMode.LOCAL,
                    state=CapabilityLifecycleState.ACTIVE,
                    version=self.provider.contract_version,
                    side_effects=item.side_effect_level,
                    entry_point={
                        "adapter": "MCPProviderAdapter",
                        "server_id": self.provider.server_id,
                        "tool_name": item.tool_name,
                        "required_permissions": sorted(item.required_permissions),
                    },
                    evidence_refs=[
                        f"mcp-discovery:{self.provider.server_id}:{item.tool_name}"
                    ],
                )
            )
        return tuple(item.capability_id for item in self.provider.bindings)

    def invoke(
        self, capability_id: str, payload: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        return self.provider.invoke(capability_id, payload)


def register_mcp_provider(
    provider: MCPProvider, inventory: CapabilityInventory
) -> MCPProviderAdapter:
    adapter = MCPProviderAdapter(provider)
    adapter.register(inventory)
    return adapter


def _validate_target(target: Any, *, allow_insecure_http: bool) -> Any:
    if not isinstance(target, str):
        return target
    value = target.strip()
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("MCP string target must be an http/https endpoint")
    if parts.username is not None or parts.password is not None:
        raise ValueError("credentials must not be embedded in MCP target URL")
    if parts.scheme == "http" and not allow_insecure_http:
        host = parts.hostname.rstrip(".").lower()
        if host not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("remote MCP service requires https or allow_insecure_http=True")
    return value


def _slug(value: str) -> str:
    rendered = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value.strip()).strip("-.").lower()
    return rendered or "tool"


def _normalize_content(blocks: Any, max_chars: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    remaining = max_chars
    for block in list(blocks or ())[:64]:
        if remaining <= 0:
            break
        kind = str(getattr(block, "type", type(block).__name__))[:64]
        text = getattr(block, "text", None)
        if isinstance(text, str):
            bounded = text[:remaining]
            remaining -= len(bounded)
            rows.append(
                {
                    "type": kind,
                    "text": bounded,
                    "truncated": len(bounded) < len(text),
                }
            )
        else:
            rows.append({"type": kind, "omitted_binary_or_resource_payload": True})
    return rows


def _bounded_json(value: Any, max_chars: int) -> Any:
    if value is None:
        return None
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, default=str)
    except (TypeError, ValueError):
        encoded = json.dumps(str(value), ensure_ascii=False)
    if len(encoded) <= max_chars:
        return value
    return {"truncated": True, "preview": encoded[:max_chars]}


def _run_async(coro: Any, *, timeout: float) -> Any:
    async def bounded() -> Any:
        return await asyncio.wait_for(coro, timeout=timeout)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(bounded())

    outcome: list[tuple[bool, Any]] = []

    def runner() -> None:
        try:
            outcome.append((True, asyncio.run(bounded())))
        except BaseException as exc:  # preserve provider exception type
            outcome.append((False, exc))

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join(timeout + 1.0)
    if thread.is_alive() or not outcome:
        raise TimeoutError("MCP operation did not terminate within Runtime bound")
    ok, value = outcome[0]
    if ok:
        return value
    raise value


__all__ = [
    "MCPProvider",
    "MCPProviderAdapter",
    "MCPToolBinding",
    "register_mcp_provider",
]
