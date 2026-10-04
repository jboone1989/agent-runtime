from __future__ import annotations

from agent_runtime.memory.semantic import (
    SemanticMemory,
    SemanticRelationalMemoryService,
)
from agent_runtime.memory.store import MemoryStore


def test_legacy_semantic_memory_api_remains_intact():
    memory = SemanticMemory()
    memory.put("browser", "JEV")
    assert memory.get("browser") == "JEV"
    assert memory.get("missing", "fallback") == "fallback"

    store = MemoryStore()
    store.semantic.put("who", "agent")
    assert store.semantic.get("who") == "agent"


def test_semantic_revision_preserves_history_and_current_claim(tmp_path):
    service = SemanticRelationalMemoryService(tmp_path)
    first = service.remember_semantic(
        text="Use TinyFish as the primary browser.",
        claim_key="browser.primary",
        source_type="OWNER_CONVERSATION",
        source_ref="operator-chat:1",
        source_text="请记住浏览器方案",
        authenticated_owner=True,
    )
    second = service.remember_semantic(
        text="Use JEV as the primary browser.",
        claim_key="browser.primary",
        source_type="OWNER_CONVERSATION",
        source_ref="operator-chat:2",
        source_text="修改之前的浏览器方案",
        authenticated_owner=True,
        supersedes=first["memory_id"],
    )

    old = service.store.get_memory(first["memory_id"])
    new = service.store.get_memory(second["memory_id"])
    assert old["status"] == "SUPERSEDED"
    assert old["superseded_by"] == second["memory_id"]
    assert new["supersedes"] == first["memory_id"]
    assert service.current_claim("browser.primary")["memory_id"] == second["memory_id"]


def test_relation_has_structured_payload_and_revision(tmp_path):
    service = SemanticRelationalMemoryService(tmp_path)
    first = service.remember_relation(
        subject="Ferro",
        predicate="uses",
        object_="TinyFish",
        source_type="OWNER_CONVERSATION",
        source_ref="operator-chat:3",
        source_text="记住 Ferro 使用 TinyFish",
        authenticated_owner=True,
    )
    second = service.remember_relation(
        subject="Ferro",
        predicate="uses",
        object_="JEV",
        source_type="OWNER_CONVERSATION",
        source_ref="operator-chat:4",
        source_text="修改 Ferro 的浏览器关系",
        authenticated_owner=True,
        supersedes=first["memory_id"],
    )

    assert service.store.get_memory(first["memory_id"])["status"] == "SUPERSEDED"
    row = service.store.get_memory(second["memory_id"])
    assert row["memory_type"] == "RELATIONAL"
    assert row["payload"]["subject"] == "Ferro"
    assert row["payload"]["predicate"] == "uses"
    assert row["payload"]["object"] == "JEV"


def test_conflicting_claim_requires_explicit_revision(tmp_path):
    service = SemanticRelationalMemoryService(tmp_path)
    service.remember_semantic(
        text="Use provider A.",
        claim_key="provider.primary",
        source_type="OWNER_CONVERSATION",
        source_ref="operator-chat:1",
        authenticated_owner=True,
    )

    try:
        service.remember_semantic(
            text="Use provider B.",
            claim_key="provider.primary",
            source_type="OWNER_CONVERSATION",
            source_ref="operator-chat:2",
            authenticated_owner=True,
        )
    except ValueError as exc:
        assert "conflict_requires_revision" in str(exc)
    else:
        raise AssertionError("conflicting semantic claim must require revision")


def test_worldview_projection_never_copies_claims_into_memory(tmp_path):
    service = SemanticRelationalMemoryService(tmp_path)

    class Mind:
        def public_projection(self):
            return {
                "beliefs": [{"proposition": "p", "confidence": 0.7}],
                "recent_belief_changes": [{"proposition": "p"}],
            }

    projection = service.worldview_projection(Mind())
    assert projection["authority"] == "virtualbrain"
    assert projection["beliefs"][0]["proposition"] == "p"
    assert service.store.list_memories() == []
