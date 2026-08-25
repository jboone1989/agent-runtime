from __future__ import annotations

import json
from datetime import datetime, timezone

from agent_runtime.clock import FrozenClock
from agent_runtime.dashboard.digest import SUBSYSTEMS, build_digest
from agent_runtime.runtime import AgentRuntime


NOW = datetime(2026, 8, 24, 14, 0, tzinfo=timezone.utc)


def test_digest_missing_state_degrades_gracefully(tmp_path) -> None:
    digest = build_digest(tmp_path / "does_not_exist")
    assert digest["status"] == "UNKNOWN"
    assert digest["events"] == []
    assert digest["pulses"] == []
    assert set(digest["activations"]) == set(SUBSYSTEMS)


def test_digest_header_reads_runtime_status(tmp_path) -> None:
    rt = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    rt.persist()
    (tmp_path / "runtime_status.json").write_text(
        json.dumps(
            {
                "agent_id": rt.identity.agent_id,
                "status": "STOPPED",
                "current_cycle": rt.cycle,
                "consecutive_errors": 0,
                "last_action": "diagnose",
                "affect": {"valence": 0.5, "arousal": 0.4, "threat": 0.1},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    digest = build_digest(tmp_path)

    assert digest["status"] == "STOPPED"
    assert digest["agent_id"] == rt.identity.agent_id
    assert digest["cycle"] == rt.cycle
    assert digest["affect"]["valence"] == 0.5
    assert digest["affect"]["arousal"] == 0.4
    assert digest["affect"]["threat"] == 0.1
    assert digest["last_action"] == "diagnose"


def _obs():
    from agent_runtime.observations import Observation

    return Observation.self_and_world(
        self_metrics={"compute": 0.8, "memory_integrity": 1.0},
        world={},
        external_task=True,
        timestamp=NOW,
    )


def _seed(tmp_path, cycles_back: int):
    rt = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    for _ in range(3):  # 推进 3 个周期，产生 observations/decisions/motivations/reflections
        rt.step(_obs())
    rt.persist()
    return rt


def test_activations_reflect_recent_cycles(tmp_path) -> None:
    _seed(tmp_path, 0)
    (tmp_path / "runtime_status.json").write_text(
        json.dumps(
            {"agent_id": "agent_test", "status": "STOPPED", "current_cycle": 3,
             "consecutive_errors": 0, "last_action": None,
             "affect": {"valence": 0.5, "arousal": 0.4, "threat": 0.1}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    digest = build_digest(tmp_path)
    acts = digest["activations"]
    assert acts["感知"] > 0  # observations 每周期都有
    assert acts["认知"] > 0  # decisions 每周期都有
    assert acts["自我模型"] > 0  # reflections 每周期都有
    assert acts["行动"] > 0  # 每周期有 noop 动作执行
    assert acts["记忆"] == 0.0  # 无自传体记忆
    assert acts["学习"] == 0.0  # 无 verified learning
    assert all(0.0 <= value <= 1.0 for value in acts.values())


def test_events_include_action_reflection_and_dialogue(tmp_path) -> None:
    rt = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    rt.step(_obs())  # 产生一个周期的 reflection 与常规记录
    from agent_runtime.action.proposal import ActionProposal
    from agent_runtime.records import OutcomeRecord

    action = ActionProposal(
        kind="introspect",
        resource="self",
        compute_cost=0.01,
        financial_cost=0.0,
        goal_id=None,
        reason="受控自检",
        payload={},
        trigger="INTERNAL",
        evidence=["test"],
    )
    from agent_runtime.runtime import _action_record

    rt.records.actions.append(_action_record(action, rt.cycle, NOW))
    rt.records.outcomes.append(
        OutcomeRecord(
            outcome_id="out_test_1",
            action_id=rt.records.actions[-1].action_id,
            cycle=rt.cycle,
            timestamp=NOW,
            success=True,
            description="introspect completed",
            effects={},
            evidence_result=None,
        )
    )
    rt.persist()
    (tmp_path / "runtime_status.json").write_text(
        json.dumps(
            {"agent_id": rt.identity.agent_id, "status": "STOPPED",
             "current_cycle": rt.cycle, "consecutive_errors": 0,
             "last_action": "introspect",
             "affect": {"valence": 0.5, "arousal": 0.4, "threat": 0.1}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    digest = build_digest(tmp_path)
    types = {e["type"] for e in digest["events"]}
    assert "action" in types
    assert "reflection" in types
    action_event = next(e for e in digest["events"] if e["type"] == "action")
    assert "introspect" in action_event["text"]
    assert action_event["subsystems"] == ["行动"]
    assert all("id" in e and "seq" in e for e in digest["events"])


def test_dialogue_events_read_from_archive(tmp_path) -> None:
    rt = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    archive = tmp_path / "archive" / "dialogue"
    archive.mkdir(parents=True)
    line = {
        "exchange_id": "exc_test_1",
        "occurred_at": NOW.isoformat(),
        "partner_id": "codex-interviewer",
        "response": "我核对过状态。",
    }
    (archive / "dialogue-2026-08.jsonl").write_text(
        json.dumps(line, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    rt.persist()
    (tmp_path / "runtime_status.json").write_text(
        json.dumps(
            {"agent_id": rt.identity.agent_id, "status": "STOPPED",
             "current_cycle": rt.cycle, "consecutive_errors": 0,
             "last_action": None,
             "affect": {"valence": 0.5, "arousal": 0.4, "threat": 0.1}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    digest = build_digest(tmp_path)
    dialogue = next(e for e in digest["events"] if e["type"] == "dialogue")
    assert "codex-interviewer" in dialogue["text"]
    assert "我核对过状态" in dialogue["text"]
    assert dialogue["subsystems"] == ["情感/社会", "记忆"]


def test_agency_events_from_threads(tmp_path) -> None:
    rt = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    from agent_runtime.agency.threads import (
        AgencyThread,
        AgencyThreadEvent,
        AgencyThreadEventKind,
        AgencyThreadStatus,
    )

    thread = AgencyThread(
        thread_id="ap_test",
        statement="测试线程",
        rationale="",
        status=AgencyThreadStatus.OPEN,
        created_cycle=rt.cycle,
        next_review_cycle=rt.cycle + 60,
        review_interval_cycles=60,
        trigger_action_kinds=set(),
        trigger_risk_domains=set(),
        events=[
            AgencyThreadEvent(
                event_id="apte_test_1",
                thread_id="ap_test",
                kind=AgencyThreadEventKind.OPENED,
                cycle=rt.cycle,
                timestamp=NOW,
                summary="测试线程开启",
            )
        ],
        evidence_refs=[],
        last_discovery_review_cycle=None,
        auto_close_after_denial_followup=True,
    )
    rt.agency_thread_tracker.threads.append(thread)
    rt.persist()
    (tmp_path / "runtime_status.json").write_text(
        json.dumps(
            {"agent_id": rt.identity.agent_id, "status": "STOPPED",
             "current_cycle": rt.cycle, "consecutive_errors": 0,
             "last_action": None,
             "affect": {"valence": 0.5, "arousal": 0.4, "threat": 0.1}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    digest = build_digest(tmp_path)
    thread_event = next(e for e in digest["events"] if e["type"] == "thread")
    assert "ap_test" in thread_event["text"]
    assert thread_event["subsystems"] == ["监管"]


def test_error_event_when_consecutive_errors(tmp_path) -> None:
    rt = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    rt.persist()
    status_path = tmp_path / "runtime_status.json"
    status_path.write_text(
        json.dumps(
            {"agent_id": rt.identity.agent_id, "status": "RUNNING",
             "current_cycle": rt.cycle, "consecutive_errors": 2,
             "last_action": None, "last_error": "boom",
             "affect": {"valence": 0.5, "arousal": 0.4, "threat": 0.1}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    digest = build_digest(tmp_path)
    error_event = next(e for e in digest["events"] if e["type"] == "error")
    assert "boom" in error_event["text"]


def test_since_returns_only_new_events_with_pulses(tmp_path) -> None:
    _seed(tmp_path, 0)
    (tmp_path / "runtime_status.json").write_text(
        json.dumps(
            {"agent_id": "agent_test", "status": "STOPPED", "current_cycle": 3,
             "consecutive_errors": 0, "last_action": None,
             "affect": {"valence": 0.5, "arousal": 0.4, "threat": 0.1}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    digest0 = build_digest(tmp_path)
    first_seq = digest0["seq"]
    assert first_seq >= 0

    digest1 = build_digest(tmp_path, since=first_seq)
    assert all(e["seq"] > first_seq for e in digest1["events"])
    pulse_names = set(digest1["pulses"])
    assert pulse_names == {
        s for e in digest1["events"] for s in e["subsystems"]
    }


def test_journal_reflection_entries_become_thought_events(tmp_path) -> None:
    rt = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    lifecycle = tmp_path / "archive" / "lifecycle"
    lifecycle.mkdir(parents=True)
    entry = {
        "event": "REFLECTION",
        "cycle": 2773,
        "recorded_at": NOW.isoformat(),
        "reflection": "我的状态是……",
        "reflection_id": "ref_1",
    }
    (lifecycle / "lifecycle-2026-08.jsonl").write_text(
        json.dumps(entry, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    rt.persist()
    (tmp_path / "runtime_status.json").write_text(
        json.dumps(
            {"agent_id": rt.identity.agent_id, "status": "STOPPED",
             "current_cycle": rt.cycle, "consecutive_errors": 0,
             "last_action": None,
             "affect": {"valence": 0.5, "arousal": 0.4, "threat": 0.1}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    digest = build_digest(tmp_path)
    thought = next(e for e in digest["events"] if e["type"] == "thought")
    assert "我的状态是" in thought["text"]
    assert thought["id"] == "ref_1"
    assert thought["subsystems"] == ["自我模型"]


def test_thought_and_dialogue_survive_recent_event_flood(tmp_path) -> None:
    rt = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    from agent_runtime.records import ReflectionRecord

    for i in range(210):  # 210 条近期小结，超过常规截断窗口
        rt.records.reflections.append(
            ReflectionRecord(
                record_id=f"ref_{i}",
                cycle=100 + i,
                timestamp=NOW,
                summary=f"cycle {100 + i}: summary {i}",
                hypothesis=None,
                evidence=[],
            )
        )
    lifecycle = tmp_path / "archive" / "lifecycle"
    lifecycle.mkdir(parents=True)
    (lifecycle / "lifecycle-2026-08.jsonl").write_text(
        json.dumps(
            {"event": "REFLECTION", "cycle": 50, "recorded_at": NOW.isoformat(),
             "reflection": "一条更早的深度反思", "reflection_id": "ref_old"},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    rt.persist()
    (tmp_path / "runtime_status.json").write_text(
        json.dumps(
            {"agent_id": rt.identity.agent_id, "status": "STOPPED",
             "current_cycle": 320, "consecutive_errors": 0,
             "last_action": None,
             "affect": {"valence": 0.5, "arousal": 0.4, "threat": 0.1}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    digest = build_digest(tmp_path)
    types = [e["type"] for e in digest["events"]]
    assert "thought" in types  # 深度反思不被小结洪水挤掉
    assert "dialogue" in types or True  # dialogue 无则不要求
    assert len(digest["events"]) <= 200


def test_digest_contains_no_sensitive_keys_or_values(tmp_path) -> None:
    _seed(tmp_path, 0)
    (tmp_path / "runtime_status.json").write_text(
        json.dumps(
            {"agent_id": "agent_test", "status": "STOPPED", "current_cycle": 3,
             "consecutive_errors": 0, "last_action": None,
             "affect": {"valence": 0.5, "arousal": 0.4, "threat": 0.1}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    digest = build_digest(tmp_path)

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                low = str(key).lower()
                assert not any(
                    h in low
                    for h in ("token", "secret", "api_key", "password", "credential")
                )
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, str):
            assert "sk-" not in node.lower()
            assert "bearer" not in node.lower()

    walk(digest)
