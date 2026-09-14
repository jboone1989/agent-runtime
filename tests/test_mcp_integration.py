from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from agent_runtime.application import ApplicationRuntime
from agent_runtime.capabilities.inventory import CapabilityInventory
from agent_runtime.capabilities.model import (
    CapabilityLifecycleState,
    SideEffectLevel,
)
from agent_runtime.integrations.mcp import (
    MCPProvider,
    MCPToolBinding,
    register_mcp_provider,
)


@dataclass
class _Tool:
    name: str
    description: str = ""
    input_schema: dict | None = None


class _Client:
    def __init__(self, tools, result):
        self.tools = tools
        self.result = result
        self.last_call = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def list_tools(self):
        return SimpleNamespace(tools=self.tools)

    async def call_tool(self, name, arguments):
        self.last_call = (name, arguments)
        return self.result


def _factory(tools, result):
    clients = []

    def make(_target):
        client = _Client(tools, result)
        clients.append(client)
        return client

    return make, clients


def test_discovery_does_not_trust_unmapped_tool_authority() -> None:
    factory, _ = _factory(
        [_Tool("read_file", input_schema={"type": "object"}), _Tool("delete_everything")],
        SimpleNamespace(is_error=False, structured_content={}, content=[]),
    )
    provider = MCPProvider(
        server_id="files",
        target="http://127.0.0.1:9000/mcp",
        bindings=(
            MCPToolBinding("workspace.read", "read_file", SideEffectLevel.READ_ONLY),
        ),
        client_factory=factory,
    )

    candidates = provider.discover_candidates()
    approved = next(row for row in candidates if row.provenance["tool_name"] == "read_file")
    unknown = next(row for row in candidates if row.provenance["tool_name"] == "delete_everything")

    assert approved.capability_id == "workspace.read"
    assert approved.side_effects is SideEffectLevel.READ_ONLY
    assert unknown.side_effects is SideEffectLevel.IRREVERSIBLE
    assert unknown.permissions == {"mcp.unclassified"}
    assert unknown.provenance["server_claim_only"] is True


def test_inventory_registers_only_explicit_approved_bindings() -> None:
    factory, _ = _factory(
        [_Tool("read_file"), _Tool("other")],
        SimpleNamespace(is_error=False, structured_content={}, content=[]),
    )
    provider = MCPProvider(
        server_id="files",
        target="http://127.0.0.1:9000/mcp",
        bindings=(
            MCPToolBinding("workspace.read", "read_file", SideEffectLevel.READ_ONLY),
        ),
        client_factory=factory,
    )
    inventory = CapabilityInventory()
    register_mcp_provider(provider, inventory)

    binding = inventory.get("workspace.read")
    assert binding is not None
    assert binding.state is CapabilityLifecycleState.ACTIVE
    assert binding.kind.name == "MCP"
    assert inventory.get("mcp.tool.files.other") is None


def test_application_runtime_enforces_mcp_side_effect_and_permissions(tmp_path) -> None:
    result = SimpleNamespace(
        is_error=False,
        structured_content={"ok": True},
        content=[SimpleNamespace(type="text", text="done")],
    )
    factory, clients = _factory([_Tool("publish")], result)
    provider = MCPProvider(
        server_id="publisher",
        target="http://127.0.0.1:9000/mcp",
        bindings=(
            MCPToolBinding(
                "content.publish",
                "publish",
                SideEffectLevel.EXTERNAL_SIDE_EFFECT,
                frozenset({"content.publish"}),
            ),
        ),
        client_factory=factory,
    )

    denied = ApplicationRuntime.bootstrap(
        tmp_path / "denied",
        subject_id="subject",
        max_side_effect_level="READ_ONLY",
        allowed_permissions=frozenset({"content.publish"}),
    )
    denied.register_provider(provider)
    with pytest.raises(PermissionError, match="exceeds runtime ceiling"):
        denied.invoke("content.publish", {"title": "x"})

    missing_permission = ApplicationRuntime.bootstrap(
        tmp_path / "missing",
        subject_id="subject",
        max_side_effect_level="EXTERNAL_SIDE_EFFECT",
    )
    missing_permission.register_provider(provider)
    with pytest.raises(PermissionError, match="missing capability permissions"):
        missing_permission.invoke("content.publish", {"title": "x"})

    allowed = ApplicationRuntime.bootstrap(
        tmp_path / "allowed",
        subject_id="subject",
        max_side_effect_level="EXTERNAL_SIDE_EFFECT",
        allowed_permissions=frozenset({"content.publish"}),
    )
    allowed.register_provider(provider)
    call = allowed.invoke("content.publish", {"title": "x"})

    assert call.payload["ok"] is True
    assert call.payload["structured_content"] == {"ok": True}
    assert clients[-1].last_call == ("publish", {"title": "x"})
    assert allowed.receipts[-1]["provider"] == "mcp:publisher"


def test_unadvertised_approved_tool_fails_closed() -> None:
    factory, _ = _factory(
        [_Tool("other")],
        SimpleNamespace(is_error=False, structured_content={}, content=[]),
    )
    provider = MCPProvider(
        server_id="files",
        target="http://127.0.0.1:9000/mcp",
        bindings=(
            MCPToolBinding("workspace.read", "read_file", SideEffectLevel.READ_ONLY),
        ),
        client_factory=factory,
    )
    with pytest.raises(ValueError, match="not advertised"):
        register_mcp_provider(provider, CapabilityInventory())


def test_unapproved_capability_cannot_be_invoked() -> None:
    factory, _ = _factory(
        [_Tool("read_file")],
        SimpleNamespace(is_error=False, structured_content={}, content=[]),
    )
    provider = MCPProvider(
        server_id="files",
        target="http://127.0.0.1:9000/mcp",
        bindings=(
            MCPToolBinding("workspace.read", "read_file", SideEffectLevel.READ_ONLY),
        ),
        client_factory=factory,
    )
    with pytest.raises(ValueError, match="unapproved MCP capability"):
        provider.invoke("mcp.tool.files.other", {})


def test_remote_plaintext_mcp_requires_explicit_opt_in() -> None:
    with pytest.raises(ValueError, match="requires https"):
        MCPProvider(
            server_id="remote",
            target="http://mcp.example/mcp",
            bindings=(MCPToolBinding("x.read", "read", SideEffectLevel.READ_ONLY),),
            client_factory=lambda _target: None,
        )
