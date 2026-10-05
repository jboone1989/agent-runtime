import asyncio
from dataclasses import asdict

import pytest

from agent_runtime.memory.durable import CanonicalMemoryStore
from agent_runtime.memory.projection_scope import ProjectionScope
from agent_runtime.memory.topics import TopicHierarchy, TopicBudgetExceeded, TopicInputChanged

SCOPE = ProjectionScope("t", "o", "a", "PRIVATE_OWNER")


def adopt(store, claim, rev, *, topics=("ai",), time="2025-01-01T00:00:00Z", status="ACTIVE", scope=SCOPE):
    store.propose_revision({"revision_id": rev, "claim_id": claim, "text": "Knowledge " + rev,
                            "recorded_at": time, "valid_from": "2024-01-01T00:00:00Z",
                            "topics": list(topics), **asdict(scope)})
    store.record_adoption({"decision_id": "d:" + rev, "revision_id": rev, "recorded_at": time,
                           "status": status, "reason": "test"})


@pytest.fixture
def system(tmp_path):
    store = CanonicalMemoryStore(tmp_path)
    tree = TopicHierarchy(store, SCOPE)
    tree.define("science", "Science", kind="DOMAIN")
    tree.define("ai", "AI", parent_id="science")
    tree.define("finance", "Finance")
    return store, tree


def test_revision_moves_multiple_memberships_and_invalidates_ancestors(system):
    store, tree = system
    adopt(store, "c", "v1", topics=("ai", "finance"))
    assert tree.update() == 1
    req = tree.summary_input("science")
    tree.save_summary(req, "AI evidence", generator={"name": "test", "version": "1"})
    assert not tree.navigate()[1]["needs_refresh"]  # science sorts after finance
    adopt(store, "c", "v2", topics=("finance",), time="2025-02-01T00:00:00Z")
    assert tree.progress()["needs_update"]
    assert tree.navigate()[1]["excerpt"] == ""
    assert tree.update() == 1
    assert tree.members("science") == []
    assert tree.members("finance")[0]["revision_id"] == "v2"
    assert tree.members("science", known_at="2025-01-15T00:00:00Z")[0]["revision_id"] == "v1"
    assert tree.navigate()[1]["needs_refresh"]
    with pytest.raises(TopicInputChanged):
        tree.save_summary(req, "stale", generator={"name": "test", "version": "1"})


def test_scope_withdrawal_paging_and_restart(system):
    store, tree = system
    foreign = ProjectionScope("t", "o", "other", "PRIVATE_OWNER")
    adopt(store, "hidden", "hidden-v1", scope=foreign)
    for i in range(4):
        adopt(store, "c" + str(i), "v" + str(i))
    assert tree.update(batch_size=2) == 2
    reopened = TopicHierarchy(store, SCOPE)
    assert reopened.update(batch_size=2) == 2
    assert reopened.update() == 0
    assert [r["claim_id"] for r in reopened.members("ai", offset=1, limit=2)] == ["c1", "c2"]
    store.record_adoption({"decision_id": "withdraw", "revision_id": "v0", "status": "RETRACTED",
                           "recorded_at": "2025-03-01T00:00:00Z", "reason": "contradicted"})
    reopened.update()
    assert [r["claim_id"] for r in reopened.members("ai", limit=2)] == ["c1", "c2"]


def test_budget_failures_preserve_sources_and_summary_refresh(system):
    store, tree = system
    adopt(store, "c1", "v1")
    adopt(store, "c2", "v2")
    tree.update()
    with pytest.raises(TopicBudgetExceeded):
        tree.summary_input("science", max_members=1)
    with pytest.raises(TopicBudgetExceeded):
        tree.summary_input("science", max_chars=10)
    calls = []
    async def generator(request):
        calls.append(request)
        return "Summary " + request["topic_id"]
    while asyncio.run(tree.refresh_one(generator, generator_version={"name": "test", "version": "1"})):
        pass
    assert len(calls) == 3
    assert all(not c["needs_refresh"] for c in tree.navigate())
    assert not asyncio.run(tree.refresh_one(generator, generator_version={"name": "test", "version": "1"}))
    with tree.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM memory_revisions").fetchone()[0] == 2


def test_adoption_during_generation_cannot_commit_stale_summary(system):
    store, tree = system
    adopt(store, "c", "v1")
    tree.update()
    req = tree.summary_input("science")
    adopt(store, "new", "v2")
    with pytest.raises(TopicInputChanged):
        tree.save_summary(req, "lost new knowledge", generator={"name": "test", "version": "1"})


def test_expiry_and_future_validity_refresh_without_new_adoptions(system, monkeypatch):
    store, tree = system
    import agent_runtime.memory.topics as module
    clock = ["2025-01-15T00:00:00.000000Z"]
    monkeypatch.setattr(module, "now", lambda: clock[0])
    for rev, start, end in (("early", "2024-01-01T00:00:00Z", "2025-02-01T00:00:00Z"),
                             ("later", "2025-02-01T00:00:00Z", None)):
        store.propose_revision({"claim_id": rev, "revision_id": rev, "text": "Knowledge " + rev,
                                "recorded_at": "2025-01-01T00:00:00Z", "valid_from": start, "valid_to": end,
                                "topics": ["ai"], **asdict(SCOPE)})
        store.record_adoption({"decision_id": "d:" + rev, "revision_id": rev, "reason": "test",
                               "status": "ACTIVE", "recorded_at": "2025-01-01T00:00:00Z"})
    tree.update()
    request = tree.summary_input("science")
    assert [b["claim_id"] for b in request["bindings"]] == ["early"]
    assert request["refresh_after"].startswith("2025-02-01")
    tree.save_summary(request, "early evidence", generator={"name": "test", "version": "1"})
    clock[0] = "2025-02-15T00:00:00.000000Z"
    assert tree.navigate()[1]["needs_refresh"]
    assert tree.update() == 0
    assert [b["claim_id"] for b in tree.summary_input("science")["bindings"]] == ["later"]
    with tree.connect() as db:
        assert db.execute("SELECT summary_id FROM topic_nodes WHERE topic_id='science'").fetchone()[0] is None
