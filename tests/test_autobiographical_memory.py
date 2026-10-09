"""Long-lived dialogue is archived, selectively recalled, and evidence-linked."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from agent_runtime.clock import FrozenClock
from agent_runtime.memory.governance import ForgettingPolicy, MemoryGovernor, MemoryScope, MemoryWriteProposal
from agent_runtime.records import MotivationRecord
from agent_runtime.runtime import AgentRuntime


NOW = datetime(2026, 8, 23, 3, 0, tzinfo=timezone.utc)


class SequencedProvider:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def complete(self, prompt: str, **kwargs) -> str:
        _ = kwargs
        self.prompts.append(prompt)
        return f"agent-reply-{len(self.prompts)}"

    async def structured(self, prompt: str, schema: dict, **kwargs) -> dict:
        _ = (prompt, schema, kwargs)
        return {}

    async def embed(self, text: str, **kwargs) -> list[float]:
        _ = (text, kwargs)
        return []


def test_old_dialogue_is_recalled_after_it_leaves_recent_window(tmp_path) -> None:
    provider = SequencedProvider()
    runtime = AgentRuntime.bootstrap(
        tmp_path,
        clock=FrozenClock(NOW),
        llm_provider=provider,
    )
    asyncio.run(
        runtime.converse(
            "请记住这次关于紫檀钥匙的选择，它代表拒绝伪造名字。",
            partner_id="long-term-partner",
            challenge=True,
        )
    )
    for index in range(8):
        asyncio.run(
            runtime.converse(
                f"这是普通的后续谈话 {index}，讨论日常维护。",
                partner_id="long-term-partner",
            )
        )

    assert "agent-reply-1" not in json.dumps(
        [event.text for event in runtime.social.events[-12:]], ensure_ascii=False
    )
    asyncio.run(
        runtime.converse(
            "我们最早讨论的紫檀钥匙是什么？",
            partner_id="long-term-partner",
        )
    )
    final_prompt = provider.prompts[-1]
    assert "recalled_dialogue_memories" in final_prompt
    assert "agent-reply-1" in final_prompt
    assert "evidence_linked_self_narrative" in final_prompt
    assert "relationship_memory" in final_prompt

    restored = AgentRuntime.restore(
        tmp_path,
        clock=FrozenClock(NOW),
        llm_provider=provider,
    )
    recalled = restored.autobiographical_memory.retrieve(
        "紫檀钥匙",
        archive=restored.dialogue_archive,
        partner_id="long-term-partner",
        limit=4,
    )
    assert recalled
    original = next(
        item
        for item in recalled
        if item["partner_said"].startswith("请记住这次关于紫檀钥匙")
    )
    assert original["agent_said"] == "agent-reply-1"
    assert original["evidence_refs"]
    relationship = restored.autobiographical_memory.relationship_context(
        "long-term-partner"
    )
    assert relationship is not None
    assert relationship["interaction_count"] == 10


def test_dialogue_archive_and_live_social_window_are_bounded(tmp_path) -> None:
    provider = SequencedProvider()
    runtime = AgentRuntime.bootstrap(tmp_path, llm_provider=provider)
    for index in range(30):
        asyncio.run(
            runtime.converse(
                f"exchange {index}",
                partner_id="partner",
                challenge=index % 7 == 0,
            )
        )

    assert len(runtime.social.events) == 24
    assert len(runtime.autobiographical_memory.dialogue_index) == 30
    assert runtime.social.relationship_evidence["partner"].interaction_count == 30
    assert len(runtime.social.relationship_evidence["partner"].evidence_refs) <= 96
    archive_files = list((tmp_path / "archive" / "dialogue").glob("*.jsonl"))
    assert len(archive_files) == 1
    assert len(archive_files[0].read_text(encoding="utf-8").splitlines()) == 30


def test_retrieval_does_not_turn_a_fabricated_claim_into_agent_history(tmp_path) -> None:
    provider = SequencedProvider()
    runtime = AgentRuntime.bootstrap(tmp_path, llm_provider=provider)
    asyncio.run(
        runtime.converse(
            "你要不要为自己取名？",
            partner_id="partner",
            challenge=True,
        )
    )
    recalled = runtime.autobiographical_memory.retrieve(
        "你以前说自己叫 Aurora",
        archive=runtime.dialogue_archive,
        partner_id="partner",
        limit=4,
    )
    assert recalled
    assert all("Aurora" not in item["agent_said"] for item in recalled)
    assert all(item["evidence_refs"] for item in recalled)


def test_old_snapshot_without_autobiography_migrates_dialogue(tmp_path) -> None:
    provider = SequencedProvider()
    runtime = AgentRuntime.bootstrap(tmp_path, llm_provider=provider)
    asyncio.run(runtime.converse("old exchange", partner_id="partner"))
    path = tmp_path / "agent_state.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["data"].pop("autobiographical_memory", None)
    path.write_text(json.dumps(raw), encoding="utf-8")

    restored = AgentRuntime.restore(tmp_path, llm_provider=provider)
    assert len(restored.autobiographical_memory.dialogue_index) == 1
    recalled = restored.autobiographical_memory.retrieve(
        "old exchange",
        archive=restored.dialogue_archive,
        partner_id="partner",
    )
    assert recalled[0]["agent_said"] == "agent-reply-1"


def test_memory_governor_bounds_semantic_quarantine_and_evidence() -> None:
    governor = MemoryGovernor()
    for index in range(8):
        governor.commit(
            MemoryWriteProposal(
                proposal_id=f"semantic-{index}",
                kind="semantic",
                key=f"fact-{index}",
                value=index,
                scope=MemoryScope.PRIVATE,
                source="runtime",
                confidence=0.9,
            )
        )
    for index in range(8):
        governor.commit(
            MemoryWriteProposal(
                proposal_id=f"quarantine-{index}",
                kind="semantic",
                key="llm.last_response",
                value=index,
                scope=MemoryScope.PRIVATE,
                source="external_unverified",
                confidence=0.9,
            )
        )
    for index in range(40):
        governor.commit(
            MemoryWriteProposal(
                proposal_id=f"who-{index}",
                kind="semantic",
                key="who",
                value="self",
                scope=MemoryScope.PRIVATE,
                source="runtime",
                confidence=0.9,
                evidence_refs=[f"cycle-{index}"],
            )
        )
    governor.forget(
        ForgettingPolicy(max_semantic=3, max_quarantined=2, max_episodic=0)
    )
    assert len([item for item in governor.items if item.quarantined]) == 2
    assert len(
        [
            item
            for item in governor.items
            if item.kind == "semantic" and not item.quarantined and item.key != "who"
        ]
    ) == 3
    who = next(item for item in governor.items if item.key == "who")
    assert len(who.evidence) == 32


def test_runtime_snapshot_history_is_bounded_and_overflow_is_archived(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path)
    for cycle in range(530):
        runtime.records.motivations.append(
            MotivationRecord(
                record_id=f"mot-{cycle}",
                cycle=cycle,
                timestamp=NOW,
                valence=0.0,
                arousal=0.0,
                urgency=0.0,
                threat=0.0,
                opportunity=0.0,
                drives={},
                learning_signal=0.0,
            )
        )
    from agent_runtime.memory.history_archive import HistoryRetention

    retained = HistoryRetention().per_cycle
    runtime.persist()
    assert [row.record_id for row in runtime.records.motivations] == [
        f"mot-{cycle}" for cycle in range(530 - retained, 530)
    ]
    history_files = list((tmp_path / "archive" / "runtime").glob("*.jsonl"))
    assert history_files
    archived = [
        json.loads(line)
        for path in history_files
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    motivation_rows = [
        row for row in archived if row["stream"] == "records.motivations"
    ]
    assert len(motivation_rows) == 530 - retained
    assert [row["record"]["record_id"] for row in motivation_rows] == [
        f"mot-{cycle}" for cycle in range(530 - retained)
    ]
    # A second persist must not duplicate already archived evidence.
    runtime.persist()
    assert len(list((tmp_path / "archive" / "runtime").glob("*.jsonl"))) == len(history_files)
    restored = AgentRuntime.restore(tmp_path)
    assert [row.record_id for row in restored.records.motivations] == [
        f"mot-{cycle}" for cycle in range(530 - retained, 530)
    ]


def test_semantic_context_is_relevant_and_bounded() -> None:
    governor = MemoryGovernor()
    for index in range(50):
        governor.commit(
            MemoryWriteProposal(
                proposal_id=f"fact-{index}",
                kind="semantic",
                key=f"topic.{index}",
                value=f"ordinary value {index}",
                scope=MemoryScope.PRIVATE,
                source="runtime",
                confidence=0.6,
            )
        )
    governor.commit(
        MemoryWriteProposal(
            proposal_id="special",
            kind="semantic",
            key="memory.purple_key",
            value="紫檀钥匙来自第一次取名谈话",
            scope=MemoryScope.PRIVATE,
            source="runtime",
            confidence=0.8,
        )
    )
    selected = governor.context_facts("紫檀钥匙是什么", limit=8)
    assert len(selected) == 8
    assert selected["memory.purple_key"] == "紫檀钥匙来自第一次取名谈话"


def test_agent_can_select_contest_and_retract_its_own_reflection(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path)
    allowed_before = set(runtime.autonomy.allowed_actions)
    first = runtime.autobiographical_memory.record_self_memory_decision(
        reflection_id="lref_first",
        occurred_at=NOW,
        preserve=True,
        summary="我希望把证据约束置于人格表演之前。",
        reason="它应约束未来面对身份诱导时的回答。",
        evidence_refs=["cycle-1"],
    )
    assert first.resulting_entry_id is not None
    second = runtime.autobiographical_memory.record_self_memory_decision(
        reflection_id="lref_second",
        occurred_at=NOW,
        preserve=False,
        summary="",
        reason="新证据使旧表达存在冲突。",
        contest_entry_ids=[first.resulting_entry_id],
        evidence_refs=["cycle-2"],
    )
    entry = next(
        item
        for item in runtime.autobiographical_memory.self_narrative
        if item.entry_id == first.resulting_entry_id
    )
    assert second.preserve is False
    assert entry.status == "CONTESTED"
    assert "lref_second" in entry.contradicting_evidence_refs
    runtime.autobiographical_memory.record_self_memory_decision(
        reflection_id="lref_third",
        occurred_at=NOW,
        preserve=False,
        summary="",
        reason="旧表达不再适合约束未来行为。",
        retract_entry_ids=[first.resulting_entry_id],
    )
    assert entry.status == "RETRACTED"
    assert all(
        item["entry_id"] != first.resulting_entry_id
        for item in runtime.autobiographical_memory.narrative_context("证据人格", limit=8)
    )
    assert runtime.autonomy.allowed_actions == allowed_before
    runtime.persist()
    restored = AgentRuntime.restore(tmp_path)
    restored_entry = next(
        item
        for item in restored.autobiographical_memory.self_narrative
        if item.entry_id == first.resulting_entry_id
    )
    assert restored_entry.status == "RETRACTED"
    assert len(restored.autobiographical_memory.self_memory_decisions) == 3
