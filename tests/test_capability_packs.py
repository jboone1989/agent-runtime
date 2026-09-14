from __future__ import annotations

from dataclasses import dataclass

import pytest

from agent_runtime import ApplicationRuntime
from agent_runtime.agents import AgentManifest
from agent_runtime.capabilities import (
    CapabilityNeed,
    CapabilityPackBindingStatus,
    CapabilityPackError,
    CapabilityPackManager,
    CapabilityPolicy,
    InstalledCapabilityPackCatalog,
    InstalledCapabilityPackSource,
    SideEffectLevel,
)
from agent_runtime.capabilities.model import CapabilityRequirement


class _Provider:
    def __init__(self, *, generate_side_effect="EXTERNAL_SIDE_EFFECT") -> None:
        self.calls = []
        self.generate_side_effect = generate_side_effect
        self.closed = 0

    def manifest(self):
        return {
            "provider": "content-media-fixture",
            "version": "1.0",
            "capabilities": [
                {
                    "capability_id": "image.search",
                    "description": "Search public images",
                    "side_effect_level": "READ_ONLY",
                    "required_permissions": [],
                },
                {
                    "capability_id": "image.generate",
                    "description": "Generate an image",
                    "side_effect_level": self.generate_side_effect,
                    "required_permissions": ["media.generate"],
                },
            ],
        }

    def invoke(self, capability_id, payload=None):
        self.calls.append((capability_id, dict(payload or {})))
        return {"ok": True, "capability_id": capability_id}

    def close(self):
        self.closed += 1


class _Pack:
    pack_id = "content-media"
    version = "1.0"

    def __init__(self, *, provider=None) -> None:
        self.provider = provider or _Provider()
        self.build_count = 0
        self.configs = []

    def manifest(self):
        return {
            "pack_id": self.pack_id,
            "version": self.version,
            "provider": "content-media-fixture",
            "license": "MIT",
            "capabilities": [
                {
                    "capability_id": "image.search",
                    "description": "Search public images",
                    "kind": "SERVICE",
                    "side_effect_level": "READ_ONLY",
                    "required_permissions": [],
                    "maintained": True,
                },
                {
                    "capability_id": "image.generate",
                    "description": "Generate an image",
                    "kind": "SERVICE",
                    "side_effect_level": "EXTERNAL_SIDE_EFFECT",
                    "required_permissions": ["media.generate"],
                    "maintained": True,
                },
            ],
        }

    def build_provider(self, config=None):
        self.build_count += 1
        self.configs.append(dict(config or {}))
        return self.provider


@dataclass
class _EntryPoint:
    name: str
    loaded: object
    group: str = "agent_runtime.capability_packs"
    load_count: int = 0

    def load(self):
        self.load_count += 1
        return self.loaded


def _catalog(pack=None, *, trusted=("content-media",), extra=()):
    pack = pack or _Pack()
    main = _EntryPoint("content-media", lambda: pack)
    return (
        InstalledCapabilityPackCatalog(
            trusted_pack_ids=trusted,
            entry_points=(main, *extra),
        ),
        pack,
        main,
    )


def _auto_read_policy():
    policy = CapabilityPolicy()
    policy.auto_acquire["read_only_skill"] = True
    return policy


def _runtime(tmp_path, *, permissions=frozenset(), ceiling="EXTERNAL_SIDE_EFFECT"):
    return ApplicationRuntime.bootstrap(
        tmp_path,
        subject_id="content-agent",
        max_side_effect_level=ceiling,
        allowed_permissions=permissions,
    )


def test_manifest_accepts_short_and_structured_capability_requirements():
    manifest = AgentManifest(
        agent_type="content-agent",
        name="Content Agent",
        version="1",
        required_capabilities=("web.search",),
        optional_capabilities=(
            {
                "capability_id": "image.generate",
                "intent": "illustrate an article",
                "max_side_effect_level": "EXTERNAL_SIDE_EFFECT",
                "required_permissions": ["media.generate"],
            },
        ),
    )
    assert manifest.required_capabilities[0].capability_id == "web.search"
    image = manifest.optional_capabilities[0]
    assert image.capability_id == "image.generate"
    assert image.max_side_effect_level == "EXTERNAL_SIDE_EFFECT"
    assert image.required_permissions == ("media.generate",)


def test_manifest_rejects_requirement_overlap():
    with pytest.raises(ValueError, match="both required and optional"):
        AgentManifest(
            agent_type="content-agent",
            name="Content Agent",
            version="1",
            required_capabilities=("web.search",),
            optional_capabilities=("web.search",),
        )


def test_catalog_never_loads_untrusted_entry_point():
    untrusted = _EntryPoint("surprise-pack", lambda: _Pack())
    catalog, _, trusted = _catalog(extra=(untrusted,))
    assert catalog.pack_ids() == ("content-media",)
    descriptor = catalog.descriptor("content-media")
    assert descriptor.provider == "content-media-fixture"
    assert trusted.load_count == 1
    assert untrusted.load_count == 0


def test_installed_pack_source_plugs_into_capability_resolver_contract():
    catalog, _, _ = _catalog()
    source = InstalledCapabilityPackSource(catalog)
    candidates = source.search(CapabilityRequirement(capability_id="image.search"))
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.capability_id == "image.search"
    assert candidate.source == "installed-pack"
    assert candidate.provenance["pack_id"] == "content-media"
    assert candidate.side_effects is SideEffectLevel.READ_ONLY


def test_read_only_need_auto_binds_only_requested_capability(tmp_path):
    catalog, pack, _ = _catalog()
    runtime = _runtime(tmp_path)
    manager = CapabilityPackManager(runtime, catalog, policy=_auto_read_policy())

    result = manager.bind_need(CapabilityNeed.simple("image.search"))

    assert result.status is CapabilityPackBindingStatus.BOUND
    assert result.pack_id == "content-media"
    assert runtime.available_capabilities() == ("image.search",)
    assert runtime.invoke("image.search", {"query": "robot"}).payload["ok"] is True
    with pytest.raises(LookupError):
        runtime.invoke("image.generate", {"prompt": "robot"})
    assert pack.build_count == 1


def test_high_authority_need_requires_approval_and_binding_does_not_grant_permission(tmp_path):
    catalog, pack, _ = _catalog()
    runtime = _runtime(tmp_path)
    policy = _auto_read_policy()
    policy.auto_acquire["executable_code"] = True
    manager = CapabilityPackManager(runtime, catalog, policy=policy)
    need = CapabilityNeed.simple(
        "image.generate",
        max_side_effect=SideEffectLevel.EXTERNAL_SIDE_EFFECT,
    )
    need.requirement.required_permissions = {"media.generate"}

    pending = manager.bind_need(need)
    assert pending.status is CapabilityPackBindingStatus.APPROVAL_REQUIRED
    assert pack.build_count == 0

    bound = manager.bind_need(need, operator_approved=True)
    assert bound.status is CapabilityPackBindingStatus.BOUND
    assert pack.build_count == 1
    with pytest.raises(PermissionError, match="media.generate"):
        runtime.invoke("image.generate", {"prompt": "robot"})


def test_explicit_runtime_permission_still_required_after_operator_approval(tmp_path):
    catalog, _, _ = _catalog()
    runtime = _runtime(tmp_path, permissions=frozenset({"media.generate"}))
    manager = CapabilityPackManager(runtime, catalog, policy=_auto_read_policy())
    need = CapabilityNeed.simple(
        "image.generate",
        max_side_effect=SideEffectLevel.EXTERNAL_SIDE_EFFECT,
    )
    need.requirement.required_permissions = {"media.generate"}
    result = manager.bind_need(need, operator_approved=True)
    assert result.status is CapabilityPackBindingStatus.BOUND
    assert runtime.invoke("image.generate", {"prompt": "robot"}).payload["ok"] is True


def test_side_effect_mismatch_fails_before_provider_construction(tmp_path):
    catalog, pack, _ = _catalog()
    runtime = _runtime(tmp_path)
    manager = CapabilityPackManager(runtime, catalog, policy=_auto_read_policy())
    result = manager.bind_need(CapabilityNeed.simple("image.generate"))
    assert result.status is CapabilityPackBindingStatus.INCOMPATIBLE
    assert result.reason == "side_effect"
    assert pack.build_count == 0


def test_provider_manifest_must_match_pack_contract(tmp_path):
    pack = _Pack(provider=_Provider(generate_side_effect="READ_ONLY"))
    catalog, _, _ = _catalog(pack)
    runtime = _runtime(tmp_path, permissions=frozenset({"media.generate"}))
    manager = CapabilityPackManager(runtime, catalog, policy=_auto_read_policy())
    need = CapabilityNeed.simple(
        "image.generate",
        max_side_effect=SideEffectLevel.EXTERNAL_SIDE_EFFECT,
    )
    need.requirement.required_permissions = {"media.generate"}
    with pytest.raises(CapabilityPackError, match="side-effect contract"):
        manager.bind_need(need, operator_approved=True)


def test_one_pack_provider_is_reused_but_capabilities_stay_individually_bound(tmp_path):
    catalog, pack, _ = _catalog()
    runtime = _runtime(tmp_path, permissions=frozenset({"media.generate"}))
    manager = CapabilityPackManager(runtime, catalog, policy=_auto_read_policy())
    assert manager.bind_need(
        CapabilityNeed.simple("image.search"), config={"endpoint": "local"}
    ).status is CapabilityPackBindingStatus.BOUND
    need = CapabilityNeed.simple(
        "image.generate",
        max_side_effect=SideEffectLevel.EXTERNAL_SIDE_EFFECT,
    )
    need.requirement.required_permissions = {"media.generate"}
    assert manager.bind_need(
        need, config={"endpoint": "local"}, operator_approved=True
    ).status is CapabilityPackBindingStatus.BOUND
    assert pack.build_count == 1
    assert runtime.available_capabilities() == ("image.generate", "image.search")


def test_pack_config_cannot_change_after_provider_is_constructed(tmp_path):
    catalog, _, _ = _catalog()
    runtime = _runtime(tmp_path, permissions=frozenset({"media.generate"}))
    manager = CapabilityPackManager(runtime, catalog, policy=_auto_read_policy())
    manager.bind_need(CapabilityNeed.simple("image.search"), config={"endpoint": "one"})
    need = CapabilityNeed.simple(
        "image.generate",
        max_side_effect=SideEffectLevel.EXTERNAL_SIDE_EFFECT,
    )
    need.requirement.required_permissions = {"media.generate"}
    with pytest.raises(CapabilityPackError, match="config changed"):
        manager.bind_need(
            need,
            config={"endpoint": "two"},
            operator_approved=True,
        )


def test_manifest_reconciliation_requires_only_required_capabilities(tmp_path):
    catalog, _, _ = _catalog()
    runtime = _runtime(tmp_path)
    manager = CapabilityPackManager(runtime, catalog, policy=_auto_read_policy())
    manifest = AgentManifest(
        agent_type="content-agent",
        name="Content Agent",
        version="1",
        required_capabilities=("image.search",),
        optional_capabilities=("video.generate",),
    )

    report = manager.reconcile_manifest(manifest)

    assert report.ready is True
    assert report.missing_required == ()
    assert report.required[0].status is CapabilityPackBindingStatus.BOUND
    assert report.optional[0].status is CapabilityPackBindingStatus.MISSING


def test_missing_required_capability_makes_manifest_not_ready(tmp_path):
    catalog, _, _ = _catalog()
    runtime = _runtime(tmp_path)
    manager = CapabilityPackManager(runtime, catalog, policy=_auto_read_policy())
    manifest = AgentManifest(
        agent_type="content-agent",
        name="Content Agent",
        version="1",
        required_capabilities=("video.generate",),
    )
    report = manager.reconcile_manifest(manifest)
    assert report.ready is False
    assert report.missing_required == ("video.generate",)


def test_pack_config_is_selected_after_provider_resolution_and_not_returned(tmp_path):
    catalog, pack, _ = _catalog()
    runtime = _runtime(tmp_path)
    manager = CapabilityPackManager(runtime, catalog, policy=_auto_read_policy())
    secret = "do-not-record"
    result = manager.bind_need(
        CapabilityNeed.simple("image.search"),
        config_by_pack={"content-media": {"token": secret}},
    )
    assert result.status is CapabilityPackBindingStatus.BOUND
    assert pack.configs == [{"token": secret}]
    assert secret not in repr(result)


def test_manager_closes_underlying_provider_once(tmp_path):
    catalog, pack, _ = _catalog()
    runtime = _runtime(tmp_path, permissions=frozenset({"media.generate"}))
    manager = CapabilityPackManager(runtime, catalog, policy=_auto_read_policy())
    manager.bind_need(CapabilityNeed.simple("image.search"))
    manager.close()
    assert pack.provider.closed == 1


def test_agent_runtime_restores_capability_requirements(tmp_path):
    from agent_runtime.runtime import AgentRuntime

    manifest = AgentManifest(
        agent_type="content-agent",
        name="Content Agent",
        version="1",
        required_capabilities=("web.search",),
        optional_capabilities=({
            "capability_id": "image.generate",
            "max_side_effect_level": "EXTERNAL_SIDE_EFFECT",
            "required_permissions": ["media.generate"],
        },),
    )
    AgentRuntime.bootstrap(tmp_path, agent_manifest=manifest).persist()

    restored = AgentRuntime.restore(tmp_path)
    assert restored.agent_manifest == manifest
    assert restored.agent_manifest.required_capabilities[0].capability_id == "web.search"
    assert restored.agent_manifest.optional_capabilities[0].required_permissions == (
        "media.generate",
    )
