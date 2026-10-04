from __future__ import annotations

import pytest

from agent_runtime.memory.episodic import EpisodeMemoryService


def test_episode_consolidates_trajectory_with_provenance(tmp_path):
    service = EpisodeMemoryService(tmp_path)
    result = service.consolidate(
        [
            {
                "event_id": "event:1",
                "actor": "operator",
                "text": "The host restarted.",
                "created_at": "2026-10-04T00:00:00Z",
            },
            {
                "event_id": "event:2",
                "actor": "agent",
                "text": "The worker restored its checkpoint.",
                "created_at": "2026-10-04T00:01:00Z",
            },
        ],
        goal="Recover durable work",
        decision="Reload checkpoint",
        actions=["reload checkpoint"],
        outcome="Worker resumed",
        lesson="Persist before restart",
        topics=["recovery"],
    )

    row = service.store.get_memory(result.memory_id)
    assert row["memory_type"] == "EPISODIC"
    assert row["evidence_refs"] == ["event:1", "event:2"]
    assert row["payload"]["decision"] == "Reload checkpoint"
    assert row["payload"]["derived_from"] == ["event:1", "event:2"]
    assert result.actors == ("operator", "agent")


def test_episode_requires_real_event_identity(tmp_path):
    service = EpisodeMemoryService(tmp_path)

    with pytest.raises(ValueError, match="at least one trajectory event"):
        service.consolidate(
            [{"actor": "agent", "text": "missing event id"}]
        )


def test_duplicate_event_ids_are_rejected(tmp_path):
    service = EpisodeMemoryService(tmp_path)

    with pytest.raises(ValueError, match="duplicate event ids"):
        service.consolidate(
            [
                {"event_id": "same", "actor": "a", "text": "one"},
                {"event_id": "same", "actor": "b", "text": "two"},
            ]
        )


def test_identical_summary_from_distinct_trajectories_is_not_deduplicated(tmp_path):
    service = EpisodeMemoryService(tmp_path)
    first = service.consolidate(
        [{"event_id": "event:a", "actor": "agent", "text": "same"}],
        goal="repeatable check",
        outcome="same result",
        lesson="same lesson",
        topics=["repeat"],
    )
    second = service.consolidate(
        [{"event_id": "event:b", "actor": "agent", "text": "same"}],
        goal="repeatable check",
        outcome="same result",
        lesson="same lesson",
        topics=["repeat"],
    )

    assert first.memory_id != second.memory_id


def test_related_episode_ids_resolve_canonical_memories(tmp_path):
    service = EpisodeMemoryService(tmp_path)
    prior = service.consolidate(
        [{"event_id": "prior:event", "actor": "agent", "text": "prior"}],
        goal="prior",
        outcome="done",
    )
    current = service.consolidate(
        [{"event_id": "current:event", "actor": "agent", "text": "current"}],
        goal="current",
        outcome="done",
        related_episode_ids=[prior.memory_id],
    )

    related = service.related(current.memory_id)
    assert [row["memory_id"] for row in related] == [prior.memory_id]
