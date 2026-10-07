from __future__ import annotations

import json
from datetime import datetime, timezone

from agent_runtime.learning.credit import CreditAssignment
from agent_runtime.learning.evidence import VerifiedLearningEvidence
from agent_runtime.learning.experience import StrategyVersion
from agent_runtime.memory.episodic import Episode
from agent_runtime.memory.history_archive import HistoryRetention
from agent_runtime.runtime import AgentRuntime


def test_history_retention_defaults_are_bounded_for_long_lived_runtime() -> None:
    retention = HistoryRetention()
    assert retention.per_cycle == 128
    assert retention.actions == 256
    assert retention.self_cognition == 128
    assert retention.capability_usage == 256
    assert retention.episodic == 256
    assert retention.learning == 512


def test_runtime_compaction_archives_old_learning_and_episodes(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(state_dir=tmp_path)
    now = datetime.now(timezone.utc)

    episodes = [
        Episode(
            episode_id=f"episode-{index}",
            timestamp=now,
            summary=f"episode {index}",
        )
        for index in range(600)
    ]
    runtime.memory.episodic.episodes = episodes
    for episode in episodes:
        runtime.memory_governor.record_episode(episode)
    runtime.learning.credit_assignments = [
        CreditAssignment(
            outcome_id=f"outcome-{index}",
            action_id=f"action-{index}",
            trace_id=f"trace-{index}",
            plan_ref=None,
            step_ref=None,
            delayed=False,
        )
        for index in range(700)
    ]
    runtime.learning.verified_evidence = [
        VerifiedLearningEvidence(
            evidence_id=f"evidence-{index}",
            agent_id=runtime.identity.agent_id,
            trace_id=f"trace-{index}",
            outcome_id=f"outcome-{index}",
            action_id=f"action-{index}",
            goal_refs=[],
            plan_ref=None,
            step_ref=None,
            success=True,
            delayed=False,
            verified_at=now,
            confidence=0.9,
            evidence_refs=[f"receipt-{index}"],
        )
        for index in range(700)
    ]
    runtime.learning.strategies._install(
        StrategyVersion(
            strategy_id="protected",
            version="1",
            body="keep old evidence hot while the strategy references it",
            active=True,
            evidence_refs=["evidence-0"],
        )
    )

    runtime._compact_live_history()

    assert len(runtime.memory.episodic.episodes) == 256
    assert len(runtime.learning.credit_assignments) == 512
    assert len(runtime.learning.verified_evidence) == 513
    assert runtime.learning.verified_evidence[0].evidence_id == "evidence-0"
    assert {
        item.evidence_id for item in runtime.learning.verified_evidence[-512:]
    } == {f"evidence-{index}" for index in range(188, 700)}

    archive_paths = sorted((tmp_path / "archive" / "runtime").glob("runtime-history-*.jsonl"))
    assert archive_paths
    rows = [
        json.loads(line)
        for path in archive_paths
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    streams = {row["stream"] for row in rows}
    assert "episodes" in streams
    assert "credit_assignments" in streams
    assert "verified_learning_evidence" in streams

    runtime.persist()
    restored = AgentRuntime.restore(tmp_path)
    assert len(restored.memory.episodic.episodes) == 256
    assert len(restored.learning.credit_assignments) == 512
    assert len(restored.learning.verified_evidence) == 513
    assert any(
        item.evidence_id == "evidence-0"
        for item in restored.learning.verified_evidence
    )
