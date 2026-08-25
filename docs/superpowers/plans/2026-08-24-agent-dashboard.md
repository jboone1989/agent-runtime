# Agent 实时仪表盘（3D 大脑 + 思考流）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为持续运行的 Agent 构建一个只读的本地浏览器仪表盘：3D 大脑（项目 9 个子系统星团 + 信息流）实时反映状态，右侧思考流展示全事件。

**Architecture:** `scripts/dashboard.py`（标准库 HTTP 服务器，只绑定 127.0.0.1）只读 `state/` 并输出 `/api/digest` JSON；纯函数摘要构建器在 `src/agent_runtime/dashboard/digest.py`（可单测）；前端静态页面在 `src/agent_runtime/dashboard/static/`（three.js 本地 vendor），每 1.5s 轮询。

**Tech Stack:** Python 3.11 标准库（http.server/json/urllib）+ 原生 JS + three.js 0.160（非模块版，本地 vendor）+ pytest。

**规格:** `docs/superpowers/specs/2026-08-24-agent-dashboard-design.md`

**执行约定:**
- 测试运行器: `C:\Users\JBoon\AppData\Local\Programs\Python\Python311\python.exe -m pytest ... -q`（工作目录 `D:\agent-runtime`）
- 本计划只新增文件，不修改任何既有源文件；git 提交只 `add` 本计划列出的新文件，绝不触碰 dirty worktree 的其他内容。
- 已有状态文件字段约定（已验证）：`agent_state.json` 顶层为 `{"__dc__":..., "data": {...}}`；`data.records.data` 含 `actions/outcomes/decisions/reflections/observations/goals_created/motivations`（每项含 `cycle`）；`data.agency_threads[*].events[*]` 含 `cycle/kind/event_id`；`data.diagnostic_coverage_probes[*].influences[*]` 含 `cycle/authorization_result/influence_id`；`data.agency_experiments[*]` 含 `scheduled_cycle/status/statement`；`data.selection_traces[*]` 含 `cycle/trace_id/chosen_action_kind/reason`；`data.verified_learning_evidence[*]` 含 `verified_at`；`data.autobiographical_memory.data.self_narrative[*]` 含 `occurred_at`；对话档案 `state/archive/dialogue/dialogue-*.jsonl` 每行含 `exchange_id/occurred_at/partner_id/response`。

---

### Task 1: digest 骨架与 header（含容错）

**Files:**
- Create: `src/agent_runtime/dashboard/__init__.py`（空文件，包标记）
- Create: `src/agent_runtime/dashboard/digest.py`
- Test: `tests/test_dashboard_digest.py`

- [ ] **Step 1: 写失败测试**

`tests/test_dashboard_digest.py`:

```python
from __future__ import annotations

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
    digest = build_digest(tmp_path)
    assert digest["status"] == "STOPPED"
    assert digest["agent_id"] == rt.identity.agent_id
    assert digest["cycle"] == rt.cycle
    assert set(digest["affect"]) == {"valence", "arousal", "threat"}
```

（`AgentRuntime.bootstrap` 会写 `runtime_status.json`（STOPPED）与 `agent_state.json`；这是真实状态结构，不需要 mock。）

- [ ] **Step 2: 运行测试确认失败**

Run: `C:\Users\JBoon\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/test_dashboard_digest.py -q`
Expected: FAIL（`ModuleNotFoundError: agent_runtime.dashboard.digest`）

- [ ] **Step 3: 最小实现**

`src/agent_runtime/dashboard/__init__.py` 内容为空（仅创建文件）。

`src/agent_runtime/dashboard/digest.py`:

```python
"""Read-only digest of Agent state for the dashboard. Never writes state."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SUBSYSTEMS = [
    "认知", "行动", "感知", "记忆", "学习", "情感/社会", "动机/存活", "自我模型", "监管",
]


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _unwrap(obj: Any) -> Any:
    if isinstance(obj, dict) and set(obj) == {"__dc__", "data"}:
        return obj["data"]
    return obj


def build_digest(state_dir: Path, since: int = 0, window_cycles: int = 30) -> dict:
    status = _load_json(Path(state_dir) / "runtime_status.json")
    if not isinstance(status, dict):
        return {
            "agent_id": None,
            "status": "UNKNOWN",
            "cycle": 0,
            "consecutive_errors": 0,
            "last_action": None,
            "affect": {"valence": 0.0, "arousal": 0.0, "threat": 0.0},
            "activations": {name: 0.0 for name in SUBSYSTEMS},
            "pulses": [],
            "events": [],
            "seq": 0,
        }
    return {
        "agent_id": status.get("agent_id"),
        "status": status.get("status"),
        "cycle": int(status.get("current_cycle") or 0),
        "consecutive_errors": int(status.get("consecutive_errors") or 0),
        "last_action": status.get("last_action"),
        "affect": {
            "valence": float((status.get("affect") or {}).get("valence") or 0.0),
            "arousal": float((status.get("affect") or {}).get("arousal") or 0.0),
            "threat": float((status.get("affect") or {}).get("threat") or 0.0),
        },
        "activations": {name: 0.0 for name in SUBSYSTEMS},
        "pulses": [],
        "events": [],
        "seq": 0,
    }
```

- [ ] **Step 4: 运行测试确认通过**

Run: `C:\Users\JBoon\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/test_dashboard_digest.py -q`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add src/agent_runtime/dashboard/__init__.py src/agent_runtime/dashboard/digest.py tests/test_dashboard_digest.py
git commit -m "feat: add dashboard digest skeleton with graceful degradation"
```

---

### Task 2: 激活映射（9 子系统信号）

**Files:**
- Modify: `src/agent_runtime/dashboard/digest.py`
- Test: `tests/test_dashboard_digest.py`（追加）

- [ ] **Step 1: 写失败测试**

在 `tests/test_dashboard_digest.py` 追加（复用 Task 1 的 import 与 NOW）：

```python
def _seed(tmp_path, cycles_back: int):
    rt = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    for _ in range(3):  # 推进 3 个周期，产生 observations/decisions/motivations/reflections
        rt.step(_obs())
    rt.persist()
    return rt


def _obs():
    from agent_runtime.observations import Observation
    return Observation.self_and_world(
        self_metrics={"compute": 0.8, "memory_integrity": 1.0},
        world={},
        external_task=True,
        timestamp=NOW,
    )


def test_activations_reflect_recent_cycles(tmp_path) -> None:
    rt = _seed(tmp_path, 0)
    digest = build_digest(tmp_path)
    acts = digest["activations"]
    assert acts["感知"] > 0  # observations 每周期都有
    assert acts["认知"] > 0  # decisions 每周期都有
    assert acts["自我模型"] > 0  # reflections 每周期都有
    assert acts["行动"] == 0.0  # 无动作执行
    assert all(0.0 <= value <= 1.0 for value in acts.values())
```

- [ ] **Step 2: 运行测试确认失败**

Run: `C:\Users\JBoon\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/test_dashboard_digest.py::test_activations_reflect_recent_cycles -q`
Expected: FAIL（activations 全为 0.0）

- [ ] **Step 3: 实现激活映射**

在 `digest.py` 顶部 import 增加 `from datetime import datetime, timedelta, timezone`，并在文件内追加：

```python
def _unwrap_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict) and isinstance(value.get("data"), list):
        return value["data"]
    return []


def _count_cycles(items: list[Any], key: str, current: int, window: int) -> int:
    return sum(
        1
        for item in items
        if isinstance(_unwrap(item).get(key), int)
        and current - window < _unwrap(item).get(key) <= current
    )


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _count_time(items: list[Any], key: str, now: datetime, seconds: int) -> int:
    count = 0
    for item in items:
        dt = _parse_dt(_unwrap(item).get(key))
        if dt is not None and now - timedelta(seconds=seconds) <= dt <= now:
            count += 1
    return count


def _count_dialogue(state_dir: Path, now: datetime, seconds: int) -> int:
    files = sorted(Path(state_dir).glob("archive/dialogue/dialogue-*.jsonl"))
    if not files:
        return 0
    count = 0
    for line in files[-1].read_text(encoding="utf-8").splitlines()[-200:]:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if _in_time_window_str(entry.get("occurred_at"), now, seconds):
            count += 1
    return count


def _in_time_window_str(value: Any, now: datetime, seconds: int) -> bool:
    dt = _parse_dt(value)
    return dt is not None and now - timedelta(seconds=seconds) <= dt <= now


def _collect_activations(
    state_dir: Path, data: dict, cycle: int, now: datetime, window_cycles: int
) -> dict[str, float]:
    records = _unwrap(data.get("records")) or {}
    seconds = window_cycles * 60
    signals: dict[str, int] = {
        "认知": _count_cycles(_unwrap_list(records.get("decisions")), "cycle", cycle, window_cycles),
        "感知": _count_cycles(_unwrap_list(records.get("observations")), "cycle", cycle, window_cycles),
        "行动": _count_cycles(_unwrap_list(records.get("actions")), "cycle", cycle, window_cycles),
        "动机/存活": (
            _count_cycles(_unwrap_list(records.get("goals_created")), "cycle", cycle, window_cycles)
            + _count_cycles(_unwrap_list(records.get("motivations")), "cycle", cycle, window_cycles)
        ),
        "自我模型": _count_cycles(_unwrap_list(records.get("reflections")), "cycle", cycle, window_cycles),
        "记忆": _count_time(
            _unwrap_list(_unwrap(data.get("autobiographical_memory"))["self_narrative"]),
            "occurred_at", now, seconds,
        ),
        "学习": _count_time(
            _unwrap_list(data.get("verified_learning_evidence")),
            "verified_at", now, seconds,
        ),
        "情感/社会": _count_dialogue(state_dir, now, seconds),
        "监管": _agency_signals(data, cycle, window_cycles),
    }
    return {name: round(min(1.0, count / 10.0), 3) for name, count in signals.items()}


def _agency_signals(data: dict, cycle: int, window_cycles: int) -> int:
    total = 0
    for thread in _unwrap_list(data.get("agency_threads")):
        total += _count_cycles(_unwrap_list(_unwrap(thread).get("events")), "cycle", cycle, window_cycles)
    for probe in _unwrap_list(data.get("diagnostic_coverage_probes")):
        p = _unwrap(probe)
        total += _count_cycles(_unwrap_list(p.get("influences")), "cycle", cycle, window_cycles)
        if isinstance(p.get("cycle"), int) and cycle - window_cycles < p.get("cycle") <= cycle:
            total += 1
    total += _count_cycles(_unwrap_list(data.get("selection_traces")), "cycle", cycle, window_cycles)
    total += _count_cycles(_unwrap_list(data.get("agency_experiments")), "scheduled_cycle", cycle, window_cycles)
    return total
```

修改 `build_digest`：在读取 status 后加 `state = _load_json(...)`、`data = state.get("data", state) if isinstance(state, dict) else {}`、`now = datetime.now(timezone.utc)`，并把返回 dict 里的 `"activations"` 改为 `_collect_activations(state_dir, data, cycle, now, window_cycles)`（`cycle` 为 `int(status.get("current_cycle") or 0)`）。

- [ ] **Step 4: 运行测试确认通过**

Run: `C:\Users\JBoon\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/test_dashboard_digest.py -q`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/agent_runtime/dashboard/digest.py tests/test_dashboard_digest.py
git commit -m "feat: add subsystem activation mapping to dashboard digest"
```

---

### Task 3: 事件流（动作/反思/对话）

**Files:**
- Modify: `src/agent_runtime/dashboard/digest.py`
- Test: `tests/test_dashboard_digest.py`（追加）

- [ ] **Step 1: 写失败测试**

在 `tests/test_dashboard_digest.py` 追加：

```python
def test_events_include_action_reflection_and_dialogue(tmp_path) -> None:
    rt = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    from agent_runtime.action.proposal import ActionProposal
    from agent_runtime.records import OutcomeRecord

    rt.records.actions.append(
        rt._action_record(ActionProposal(kind="introspect", resource="self", compute_cost=0.01, financial_cost=0.0, goal_id=None, reason="受控自检", payload={}, trigger="INTERNAL", evidence=["test"]), rt.cycle, NOW)
    )
    rt.records.outcomes.append(
        OutcomeRecord(outcome_id="out_test_1", action_id=rt.records.actions[-1].action_id, cycle=rt.cycle, timestamp=NOW, success=True, description="introspect completed", effects={}, evidence_result=None)
    )
    rt.persist()

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

    digest = build_digest(tmp_path)
    dialogue = next(e for e in digest["events"] if e["type"] == "dialogue")
    assert "codex-interviewer" in dialogue["text"]
    assert "我核对过状态" in dialogue["text"]
    assert dialogue["subsystems"] == ["情感/社会", "记忆"]
```

（测试文件顶部需加 `import json`。）

- [ ] **Step 2: 运行测试确认失败**

Run: `C:\Users\JBoon\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/test_dashboard_digest.py -q`
Expected: FAIL（events 为空 / KeyError）

- [ ] **Step 3: 实现事件收集**

在 `digest.py` 追加（`_action_record` 为 `AgentRuntime` 私有方法，测试用它构造真实 `ActionRecord`；digest 只读记录字段）：

```python
def _est_cycle(value: Any, current: int, now: datetime) -> float:
    dt = _parse_dt(value)
    if dt is None:
        return float(current)
    return current - (now - dt).total_seconds() / 60.0


def _read_dialogue(state_dir: Path, limit: int = 30) -> list[dict]:
    files = sorted(Path(state_dir).glob("archive/dialogue/dialogue-*.jsonl"))
    if not files:
        return []
    entries: list[dict] = []
    for line in files[-1].read_text(encoding="utf-8").splitlines()[-limit:]:
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def _collect_events(state_dir: Path, data: dict, status: dict, now: datetime) -> list[dict]:
    cycle = int(status.get("current_cycle") or 0)
    records = _unwrap(data.get("records")) or {}
    outcome_by_action = {
        _unwrap(o).get("action_id"): _unwrap(o)
        for o in _unwrap_list(records.get("outcomes"))[-400:]
    }
    events: list[dict] = []
    for a in _unwrap_list(records.get("actions"))[-200:]:
        aa = _unwrap(a)
        oo = outcome_by_action.get(aa.get("action_id")) or {}
        er = oo.get("evidence_result")
        mark = "✓" if oo.get("success") else "✗"
        suffix = f" → {mark}" + (f" 证据:{er}" if er else "")
        events.append({
            "id": aa.get("action_id"),
            "type": "action",
            "cycle": aa.get("cycle"),
            "ts": aa.get("timestamp"),
            "text": f"{aa.get('kind')} [{aa.get('trigger')}] — {aa.get('reason')}{suffix}",
            "subsystems": ["行动"],
        })
    for r in _unwrap_list(records.get("reflections"))[-200:]:
        rr = _unwrap(r)
        events.append({
            "id": rr.get("record_id"),
            "type": "reflection",
            "cycle": rr.get("cycle"),
            "ts": rr.get("timestamp"),
            "text": str(rr.get("summary")),
            "subsystems": ["自我模型"],
        })
    for d in _read_dialogue(state_dir):
        events.append({
            "id": d.get("exchange_id"),
            "type": "dialogue",
            "cycle": _est_cycle(d.get("occurred_at"), cycle, now),
            "ts": d.get("occurred_at"),
            "text": f"{d.get('partner_id')}: {d.get('response')}",
            "subsystems": ["情感/社会", "记忆"],
        })
    ranks = {"action": 0, "reflection": 1, "dialogue": 2}
    events.sort(key=lambda e: (e["cycle"] or cycle, ranks.get(e["type"], 9), str(e["id"])))
    events = events[-200:]
    for index, event in enumerate(events):
        event["seq"] = index
    return events
```

修改 `build_digest`：`"events": _collect_events(state_dir, data, status, now)`；`"pulses": [name for event in events if event["seq"] > since for name in event["subsystems"]]`（`since` 为参数已有）；`"seq": max((e["seq"] for e in events), default=0)`。

- [ ] **Step 4: 运行测试确认通过**

Run: `C:\Users\JBoon\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/test_dashboard_digest.py -q`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/agent_runtime/dashboard/digest.py tests/test_dashboard_digest.py
git commit -m "feat: add action/reflection/dialogue events to dashboard digest"
```

---

### Task 4: 事件流（监管/实验/选择/错误）

**Files:**
- Modify: `src/agent_runtime/dashboard/digest.py`
- Test: `tests/test_dashboard_digest.py`（追加）

- [ ] **Step 1: 写失败测试**

在 `tests/test_dashboard_digest.py` 追加：

```python
def test_agency_events_from_threads_probes_experiments_traces(tmp_path) -> None:
    rt = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    from agent_runtime.agency.threads import AgencyThread, AgencyThreadEvent, AgencyThreadEventKind, AgencyThreadStatus

    thread = AgencyThread(
        thread_id="ap_test", statement="测试线程", rationale="", status=AgencyThreadStatus.OPEN,
        created_cycle=rt.cycle, next_review_cycle=rt.cycle + 60, review_interval_cycles=60,
        trigger_action_kinds=set(), trigger_risk_domains=set(),
        events=[AgencyThreadEvent(event_id="apte_test_1", thread_id="ap_test", kind=AgencyThreadEventKind.OPENED, cycle=rt.cycle, timestamp=NOW)],
        evidence_refs=[], last_discovery_review_cycle=None, auto_close_after_denial_followup=True,
    )
    rt.agency_thread_tracker.threads.append(thread)
    rt.persist()

    digest = build_digest(tmp_path)
    thread_event = next(e for e in digest["events"] if e["type"] == "thread")
    assert "ap_test" in thread_event["text"]
    assert thread_event["subsystems"] == ["监管"]


def test_error_event_when_consecutive_errors(tmp_path) -> None:
    rt = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    rt.persist()
    status_path = tmp_path / "runtime_status.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    status["consecutive_errors"] = 2
    status["last_error"] = "boom"
    status_path.write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")

    digest = build_digest(tmp_path)
    error_event = next(e for e in digest["events"] if e["type"] == "error")
    assert "boom" in error_event["text"]
```

（`AgencyThread` 构造器字段如与当前版本不一致，执行时以 `src/agent_runtime/agency/threads.py` 实际签名为准修正测试，实现代码不变。）

- [ ] **Step 2: 运行测试确认失败**

Run: `C:\Users\JBoon\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/test_dashboard_digest.py -q`
Expected: FAIL（找不到 thread/error 事件）

- [ ] **Step 3: 实现**

在 `_collect_events` 的 `ranks` 之前追加：

```python
    for t in _unwrap_list(data.get("agency_threads")):
        tt = _unwrap(t)
        for e in _unwrap_list(tt.get("events"))[-40:]:
            ee = _unwrap(e)
            kind = ee.get("kind")
            kind_value = kind.get("value") if isinstance(kind, dict) else kind
            events.append({
                "id": ee.get("event_id"),
                "type": "thread",
                "cycle": ee.get("cycle"),
                "ts": ee.get("timestamp"),
                "text": f"线程 {tt.get('thread_id')} · {kind_value}",
                "subsystems": ["监管"],
            })
    for p in _unwrap_list(data.get("diagnostic_coverage_probes")):
        pp = _unwrap(p)
        for inf in _unwrap_list(pp.get("influences"))[-40:]:
            ii = _unwrap(inf)
            events.append({
                "id": ii.get("influence_id"),
                "type": "coverage",
                "cycle": ii.get("cycle"),
                "ts": None,
                "text": f"覆盖 {pp.get('target_path')} · {ii.get('authorization_result')} (probe {pp.get('probe_id')})",
                "subsystems": ["监管"],
            })
    for e in _unwrap_list(data.get("agency_experiments"))[-20:]:
        ee = _unwrap(e)
        st = ee.get("status")
        st_value = st.get("value") if isinstance(st, dict) else st
        events.append({
            "id": ee.get("experiment_id"),
            "type": "experiment",
            "cycle": ee.get("scheduled_cycle"),
            "ts": None,
            "text": f"实验 {ee.get('experiment_id')} · {st_value} — {str(ee.get('statement'))[:80]}",
            "subsystems": ["监管"],
        })
    for t in _unwrap_list(data.get("selection_traces"))[-100:]:
        tt = _unwrap(t)
        reason = tt.get("reason")
        reason_value = reason.get("value") if isinstance(reason, dict) else reason
        events.append({
            "id": tt.get("trace_id"),
            "type": "selection",
            "cycle": tt.get("cycle"),
            "ts": tt.get("timestamp"),
            "text": f"选择 {tt.get('chosen_action_kind')} · {reason_value}",
            "subsystems": ["监管"],
        })
    if status.get("consecutive_errors"):
        events.append({
            "id": f"err-{cycle}",
            "type": "error",
            "cycle": cycle,
            "ts": now.isoformat(),
            "text": f"连续错误 {status.get('consecutive_errors')}: {status.get('last_error')}",
            "subsystems": ["动机/存活"],
        })
```

并把 `ranks` 改为 `{"action": 0, "reflection": 1, "dialogue": 2, "thread": 3, "coverage": 4, "experiment": 5, "selection": 6, "error": 7}`。

- [ ] **Step 4: 运行测试确认通过**

Run: `C:\Users\JBoon\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/test_dashboard_digest.py -q`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/agent_runtime/dashboard/digest.py tests/test_dashboard_digest.py
git commit -m "feat: add agency and error events to dashboard digest"
```

---

### Task 5: 增量 since、pulse 与敏感字段扫描

**Files:**
- Modify: `src/agent_runtime/dashboard/digest.py`（若 Step 3 发现需要）
- Test: `tests/test_dashboard_digest.py`（追加）

- [ ] **Step 1: 写失败测试**

在 `tests/test_dashboard_digest.py` 追加：

```python
def test_since_returns_only_new_events_with_pulses(tmp_path) -> None:
    rt = _seed(tmp_path, 0)
    digest0 = build_digest(tmp_path)
    first_seq = digest0["seq"]
    assert first_seq >= 0

    digest1 = build_digest(tmp_path, since=first_seq)
    assert all(e["seq"] > first_seq for e in digest1["events"])
    pulse_names = set(digest1["pulses"])
    assert pulse_names == {s for e in digest1["events"] for s in e["subsystems"]}


def test_digest_contains_no_sensitive_keys_or_values(tmp_path) -> None:
    rt = _seed(tmp_path, 0)
    rt.persist()
    digest = build_digest(tmp_path)

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                low = str(key).lower()
                assert not any(h in low for h in ("token", "secret", "api_key", "password", "credential"))
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, str):
            assert "sk-" not in node.lower()
            assert "bearer" not in node.lower()

    walk(digest)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `C:\Users\JBoon\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/test_dashboard_digest.py -q`
Expected: `test_since_returns_only_new_events_with_pulses` FAIL（pulses 恒为空——Task 3 的 pulses 表达式在 `since` 大于 seq 时不返回任何事件，需按下方语义修正）

- [ ] **Step 3: 实现增量语义**

把 `build_digest` 中 `"events"` 与 `"pulses"` 的构造改为：

```python
    all_events = _collect_events(state_dir, data, status, now)
    events = [e for e in all_events if e["seq"] > since]
    pulses: list[str] = []
    for event in events:
        for name in event["subsystems"]:
            if name not in pulses:
                pulses.append(name)
```

（`all_events` 保持服务端完整 seq 排序；`seq` 返回 `max((e["seq"] for e in all_events), default=0)`。注意：seq 是每轮重算的排序位置，客户端必须按 `id` 去重——见 Task 7。）

- [ ] **Step 4: 运行测试确认通过**

Run: `C:\Users\JBoon\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/test_dashboard_digest.py -q`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add src/agent_runtime/dashboard/digest.py tests/test_dashboard_digest.py
git commit -m "feat: add incremental since and pulse semantics to dashboard digest"
```

---

### Task 6: 服务器与 CLI 入口

**Files:**
- Create: `src/agent_runtime/dashboard/server.py`
- Create: `scripts/dashboard.py`
- Test: `tests/test_dashboard_server.py`

- [ ] **Step 1: 写失败测试**

`tests/test_dashboard_server.py`:

```python
from __future__ import annotations

import json
from urllib.request import urlopen

from agent_runtime.dashboard.server import DashboardServer


def test_server_serves_digest_and_index(tmp_path) -> None:
    server = DashboardServer(state_dir=tmp_path, port=0)
    server.start()
    try:
        base = f"http://127.0.0.1:{server.port}"
        with urlopen(base + "/api/digest", timeout=5) as resp:
            assert resp.status == 200
            payload = json.loads(resp.read().decode("utf-8"))
            assert payload["status"] == "UNKNOWN"  # 空状态目录
            assert set(payload["activations"].keys())
        with urlopen(base + "/", timeout=5) as resp:
            assert resp.status == 200
            body = resp.read().decode("utf-8")
            assert "<!DOCTYPE html>" in body or "<html" in body
        with urlopen(base + "/api/digest?since=999", timeout=5) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            assert payload["events"] == []
    finally:
        server.stop()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `C:\Users\JBoon\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/test_dashboard_server.py -q`
Expected: FAIL（`ModuleNotFoundError: agent_runtime.dashboard.server`）

- [ ] **Step 3: 实现服务器**

`src/agent_runtime/dashboard/server.py`:

```python
"""Read-only local HTTP server for the Agent dashboard."""
from __future__ import annotations

import json
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .digest import build_digest

STATIC_DIR = Path(__file__).parent / "static"


class _Handler(SimpleHTTPRequestHandler):
    server_state_dir: Path

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def do_GET(self) -> None:  # noqa: N802 — http.server API
        parsed = urlsplit(self.path)
        if parsed.path == "/api/digest":
            query = parse_qs(parsed.query)
            since = int(query.get("since", ["0"])[0] or "0")
            payload = build_digest(self.server_state_dir, since=since)
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def log_message(self, format, *args):  # noqa: A002 — quiet by default
        pass


class DashboardServer:
    def __init__(self, state_dir: Path, port: int = 8765) -> None:
        self.state_dir = Path(state_dir).resolve()
        handler = type("BoundHandler", (_Handler,), {"server_state_dir": self.state_dir})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
        self.port = self.httpd.server_address[1]
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
```

`scripts/dashboard.py`:

```python
"""Serve the read-only Agent dashboard at http://127.0.0.1:8765 ."""
from __future__ import annotations

import argparse
from pathlib import Path

from agent_runtime.dashboard.server import DashboardServer


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, default=Path("state"))
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = DashboardServer(state_dir=args.state_dir, port=args.port)
    print(
        f"Agent dashboard: http://127.0.0.1:{server.port} "
        f"(state: {server.state_dir}, read-only)"
    )
    try:
        server.httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 运行测试确认通过**

Run: `C:\Users\JBoon\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/test_dashboard_server.py -q`
Expected: 1 passed

- [ ] **Step 5: Commit**

```bash
git add src/agent_runtime/dashboard/server.py scripts/dashboard.py tests/test_dashboard_server.py
git commit -m "feat: add read-only dashboard HTTP server and CLI"
```

---

### Task 7: 前端页面（布局 + 思考流 + 大脑渲染 + 轮询接线）

**Files:**
- Create: `src/agent_runtime/dashboard/static/index.html`
- Create: `src/agent_runtime/dashboard/static/style.css`
- Create: `src/agent_runtime/dashboard/static/app.js`

（大脑渲染代码移植自已批准的视觉原型 `.superpowers/brainstorm/896-1787581399/content/brain-style-v4.html`——本任务给出完整最终代码，效果与 v4 一致：可拖动、光晕、扫描环、信息流。）

- [ ] **Step 1: 写 index.html**

```html
<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Agent Runtime · 实时仪表盘</title>
<link rel="stylesheet" href="style.css">
</head>
<body>
<header class="hud">
  <div class="hud-item"><span class="hud-label">状态</span><span id="hud-status">—</span></div>
  <div class="hud-item"><span class="hud-label">周期</span><span id="hud-cycle">—</span></div>
  <div class="hud-item"><span class="hud-label">连续错误</span><span id="hud-errors">—</span></div>
  <div class="hud-item"><span class="hud-label">情感</span><span id="hud-affect">—</span></div>
  <div class="hud-item"><span class="hud-label">最后动作</span><span id="hud-action">—</span></div>
</header>
<main>
  <section class="brain-pane">
    <canvas id="brain"></canvas>
    <div class="legend" id="legend"></div>
  </section>
  <section class="stream-pane">
    <div class="stream-toolbar">
      <span class="stream-title">思考流</span>
      <div class="chips" id="chips"></div>
      <label class="autoscroll"><input type="checkbox" id="autoscroll" checked> 自动滚动</label>
    </div>
    <div class="stream" id="stream"></div>
    <div class="reconnect" id="reconnect" hidden>连接中断，正在重试…</div>
  </section>
</main>
<script src="vendor/three.min.js"></script>
<script src="app.js"></script>
</body>
</html>
```

- [ ] **Step 2: 写 style.css**

```css
* { box-sizing: border-box; }
body { margin: 0; background: #05070d; color: #e8eaf0; font-family: "Segoe UI", "Microsoft YaHei", sans-serif; height: 100vh; display: flex; flex-direction: column; }
.hud { display: flex; gap: 18px; padding: 10px 18px; background: #0a0f1a; border-bottom: 1px solid #1a2233; font-size: 13px; }
.hud-item { display: flex; gap: 6px; }
.hud-label { color: #5d6780; }
main { flex: 1; display: flex; min-height: 0; }
.brain-pane { flex: 1.2; position: relative; display: flex; flex-direction: column; }
#brain { flex: 1; width: 100%; display: block; cursor: grab; background: radial-gradient(circle at 50% 45%, #0d1424 0%, #04060c 70%); }
#brain.dragging { cursor: grabbing; }
.legend { display: flex; flex-wrap: wrap; gap: 7px; padding: 8px 14px; }
.legend span { font-size: 12px; color: #aeb8cc; background: #101627; border: 1px solid #1e2740; padding: 3px 9px; border-radius: 18px; }
.legend i { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 5px; box-shadow: 0 0 6px currentColor; }
.stream-pane { flex: 0.9; display: flex; flex-direction: column; border-left: 1px solid #1a2233; min-width: 320px; }
.stream-toolbar { display: flex; align-items: center; gap: 10px; padding: 10px 14px; border-bottom: 1px solid #1a2233; flex-wrap: wrap; }
.stream-title { font-weight: 600; }
.chips { display: flex; gap: 6px; flex-wrap: wrap; }
.chip { font-size: 12px; padding: 3px 10px; border-radius: 14px; background: #101627; border: 1px solid #1e2740; cursor: pointer; color: #8a93a6; user-select: none; }
.chip.active { color: #e8eaf0; border-color: #3d4c6b; }
.autoscroll { font-size: 12px; color: #8a93a6; margin-left: auto; }
.stream { flex: 1; overflow-y: auto; padding: 10px 14px; display: flex; flex-direction: column; gap: 8px; }
.event { font-size: 13px; padding: 8px 10px; background: #0d1420; border: 1px solid #182133; border-radius: 8px; line-height: 1.55; }
.event .meta { color: #5d6780; font-size: 11px; margin-bottom: 3px; }
.event .subs { display: inline-block; margin-left: 8px; font-size: 11px; padding: 0 7px; border-radius: 10px; border: 1px solid; }
.event.flash { border-color: #ffb347; }
.reconnect { padding: 8px 14px; color: #ff6b6b; font-size: 13px; }
```

- [ ] **Step 3: 写 app.js（完整代码）**

```javascript
"use strict";
// ===== 常量：子系统与连线（与 digest.py 的 SUBSYSTEMS 一致） =====
const SUBS = [
  ["认知",       "#ffb347", -0.75, 0.70, 0.55, 0.38],
  ["行动",       "#ff5f56",  0.75, 0.50, 0.65, 0.36],
  ["感知",       "#ab7bff",  0.40, 0.25,-1.05, 0.36],
  ["记忆",       "#66d9a0", -1.05,-0.25, 0.45, 0.34],
  ["学习",       "#4dd0e1",  1.05,-0.35, 0.25, 0.34],
  ["情感/社会",  "#ff6b9d", -0.30, 0.10, 0.50, 0.42],
  ["动机/存活",  "#ffd166",  0.00,-1.05,-0.45, 0.36],
  ["自我模型",   "#b0b7ff", -0.45, 1.05,-0.10, 0.34],
  ["监管",       "#7fd4ff",  0.50, 1.05,-0.20, 0.34],
];
const LINKS = {0:[1,3,7], 1:[2,8], 2:[0,5], 3:[4,7], 4:[7,8], 5:[0,8], 6:[8,0], 7:[8,0], 8:[1,2,5]};
const SUBS_COLOR = SUBS.map(s => new THREE.Color(s[1]));
const ENVELOPE = [
  [ 0.62, 0.35, 0.15, 1.05, 1.15, 1.05], [-0.62, 0.35, 0.15, 1.05, 1.15, 1.05],
  [ 0.95,-0.05, 0.55, 0.62, 0.62, 0.75], [-0.95,-0.05, 0.55, 0.62, 0.62, 0.75],
  [ 0.35, 0.30,-1.15, 0.62, 0.60, 0.55], [-0.35, 0.30,-1.15, 0.62, 0.60, 0.55],
  [ 0.00,-0.62,-1.05, 0.85, 0.45, 0.48], [ 0.00,-1.30,-0.25, 0.26, 0.55, 0.32],
];

// ===== 3D 大脑 =====
const canvas = document.getElementById("brain");
const renderer = new THREE.WebGLRenderer({canvas, antialias:true});
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
const scene = new THREE.Scene();
scene.add(new THREE.AmbientLight(0x99aacc, 1.5));
const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 80);
camera.position.set(0, 0.3, 6.2);
const orbit = new THREE.Group(); scene.add(orbit);
const brain = new THREE.Group(); orbit.add(brain);

function shellPos(env, N, b0, b1) {
  const pos = new Float32Array(N*3);
  const [cx,cy,cz,rx,ry,rz] = env;
  for (let i=0;i<N;i++){
    const th = Math.random()*Math.PI*2, ph = Math.acos(2*Math.random()-1);
    const s = b0+(b1-b0)*Math.random();
    pos[i*3]=cx+rx*s*Math.sin(ph)*Math.cos(th);
    pos[i*3+1]=cy+ry*s*Math.sin(ph)*Math.sin(th);
    pos[i*3+2]=cz+rz*s*Math.cos(ph);
  }
  return pos;
}
function glowTexture(hex) {
  const cv = document.createElement("canvas"); cv.width = cv.height = 128;
  const g = cv.getContext("2d");
  const grad = g.createRadialGradient(64,64,0,64,64,64);
  grad.addColorStop(0, hex); grad.addColorStop(0.25, hex+"88"); grad.addColorStop(1, hex+"00");
  g.fillStyle = grad; g.fillRect(0,0,128,128);
  return new THREE.CanvasTexture(cv);
}
// 星空
{
  const pos = new Float32Array(500*3);
  for (let i=0;i<500;i++){ pos[i*3]=(Math.random()-0.5)*30; pos[i*3+1]=(Math.random()-0.5)*20; pos[i*3+2]=-8-Math.random()*12; }
  const g = new THREE.BufferGeometry(); g.setAttribute("position", new THREE.BufferAttribute(pos,3));
  scene.add(new THREE.Points(g, new THREE.PointsMaterial({color:0x8fa0c0, size:0.02, transparent:true, opacity:0.7})));
}
// 脑组织基底
{
  const counts=[560,560,240,240,260,260,320,170];
  let total=0; counts.forEach(n=>total+=n*3);
  const merged=new Float32Array(total); let off=0;
  ENVELOPE.forEach((env,i)=>{ const p=shellPos(env,counts[i],0.90,1.0); merged.set(p,off); off+=p.length; });
  const g=new THREE.BufferGeometry(); g.setAttribute("position", new THREE.BufferAttribute(merged,3));
  brain.add(new THREE.Points(g, new THREE.PointsMaterial({color:0x5f8fd8, size:0.04, transparent:true, opacity:0.32, blending:THREE.AdditiveBlending, depthWrite:false})));
}
// 子系统星团 + 光晕
const subGroups = SUBS.map(s => {
  const pos = shellPos([s[2],s[3],s[4],s[5],s[5]*0.9,s[5]*0.95], 260, 0, 1);
  const g = new THREE.BufferGeometry(); g.setAttribute("position", new THREE.BufferAttribute(pos,3));
  const m = new THREE.PointsMaterial({color:s[1], size:0.07, transparent:true, opacity:0.92, blending:THREE.AdditiveBlending, depthWrite:false});
  m._base=0.92;
  const pts = new THREE.Points(g,m); brain.add(pts);
  const halo = new THREE.Sprite(new THREE.SpriteMaterial({map:glowTexture(s[1]), transparent:true, opacity:0.35, blending:THREE.AdditiveBlending, depthWrite:false}));
  halo.scale.setScalar(s[5]*4.2); halo.position.set(s[2],s[3],s[4]);
  halo.material._base=0.35;
  brain.add(halo);
  return {points:pts, material:m, halo, level:0};
});
// 扫描环
const rings = [];
[[1.95,0.9,0.2],[1.95,-0.4,0.5],[1.6,1.0,-0.6]].forEach(([r,ix,iz])=>{
  const ring = new THREE.Mesh(new THREE.TorusGeometry(r,0.006,8,120),
    new THREE.MeshBasicMaterial({color:0x4d7fd8, transparent:true, opacity:0.4, blending:THREE.AdditiveBlending}));
  ring.rotation.x = Math.PI/2*ix; ring.rotation.z = Math.PI/2*iz;
  orbit.add(ring); rings.push(ring);
});
// 信息流
const FLOW_N=900;
const flow={t:new Float32Array(FLOW_N),dur:new Float32Array(FLOW_N),from:new Int32Array(FLOW_N),to:new Int32Array(FLOW_N),mid:new Float32Array(FLOW_N*3)};
for(let i=0;i<FLOW_N;i++)flow.t[i]=-Math.random();
const fGeo=new THREE.BufferGeometry();
const fPos=new Float32Array(FLOW_N*3),fCol=new Float32Array(FLOW_N*3);
fGeo.setAttribute("position",new THREE.BufferAttribute(fPos,3));
fGeo.setAttribute("color",new THREE.BufferAttribute(fCol,3));
const flowPts = new THREE.Points(fGeo, new THREE.PointsMaterial({size:0.06,vertexColors:true,transparent:true,opacity:1,blending:THREE.AdditiveBlending,depthWrite:false}));
scene.add(flowPts);
const centers = SUBS.map(s => new THREE.Vector3(s[2],s[3],s[4]));
const WHITE = new THREE.Color(0xffffff);
let acc = 0;

function emitFlow(fromIdx, count) {
  const targets = LINKS[fromIdx] || [];
  if (!targets.length) return;
  for (let n=0;n<count;n++){
    const idx = flow.t.findIndex(v=>v<0);
    if (idx<0) return;
    flow.from[idx]=fromIdx; flow.to[idx]=targets[Math.floor(Math.random()*targets.length)];
    const a=centers[fromIdx], b=centers[flow.to[idx]];
    flow.mid[idx*3]=(a.x+b.x)/2; flow.mid[idx*3+1]=(a.y+b.y)/2+0.9; flow.mid[idx*3+2]=(a.z+b.z)/2+0.2;
    flow.t[idx]=0; flow.dur[idx]=0.8+Math.random()*0.7;
  }
}
function setActivation(levels) {
  SUBS.forEach((s, i) => { subGroups[i].level = Math.max(0, Math.min(1, levels[s[0]] || 0)); });
}
// 拖动
let dragging=false, px=0, py=0, velX=0, velY=0, rotX=0, rotY=0;
canvas.addEventListener("pointerdown", e=>{dragging=true;px=e.clientX;py=e.clientY;velX=velY=0;canvas.classList.add("dragging");canvas.setPointerCapture(e.pointerId);});
canvas.addEventListener("pointermove", e=>{if(!dragging)return;const dx=e.clientX-px,dy=e.clientY-py;velX=dx*0.005;velY=dy*0.005;rotY+=velX;rotX+=velY;px=e.clientX;py=e.clientY;});
canvas.addEventListener("pointerup", ()=>{dragging=false;canvas.classList.remove("dragging");});
canvas.addEventListener("wheel", e=>{e.preventDefault();camera.position.z=Math.min(11,Math.max(3.4,camera.position.z+e.deltaY*0.005));},{passive:false});
canvas.addEventListener("dblclick", ()=>{rotX=rotY=0;camera.position.z=6.2;});
function resize(){ renderer.setSize(canvas.clientWidth, canvas.clientHeight, false); camera.aspect=canvas.clientWidth/canvas.clientHeight; camera.updateProjectionMatrix(); }
window.addEventListener("resize", resize); resize();
function bez(p0,p1,p2,t){const u=1-t;return[u*u*p0[0]+2*u*t*p1[0]+t*t*p2[0],u*u*p0[1]+2*u*t*p1[1]+t*t*p2[1],u*u*p0[2]+2*u*t*p1[2]+t*t*p2[2]];}
function tick(){
  requestAnimationFrame(tick);
  const t=performance.now()/1000;
  if(!dragging){rotY+=0.0016;velX*=0.94;velY*=0.94;}else{velX*=0.85;velY*=0.85;}
  rotY+=velX; rotX+=velY;
  orbit.rotation.y=rotY; orbit.rotation.x=rotX;
  brain.scale.setScalar(1+0.01*Math.sin(t*0.8));
  subGroups.forEach((sg,i)=>{
    const level=sg.level;
    sg.material.opacity=sg.material._base*(0.3+0.7*level);
    sg.material.size=0.06+0.04*level;
    sg.halo.material.opacity=sg.halo.material._base*(0.25+0.75*level);
    sg.halo.scale.setScalar(SUBS[i][5]*4.2*(1+0.25*level));
  });
  rings.forEach((r,i)=>{r.rotation.z+=0.003*(i%2?1:-1);r.rotation.x+=0.0015*(i%2?-1:1);});
  acc += 1.5;
  while(acc>=1){
    acc-=1;
    const act = subGroups.reduce((best,sg,i)=> sg.level>subGroups[best].level? i:best, 0);
    if (subGroups[act].level > 0.05) emitFlow(act, 3);
  }
  for(let i=0;i<FLOW_N;i++){
    if(flow.t[i]<0){fCol[i*3]=fCol[i*3+1]=fCol[i*3+2]=0;fPos[i*3]=99;continue;}
    flow.t[i]+=0.016/flow.dur[i];
    if(flow.t[i]>=1){flow.t[i]=-1;fCol[i*3]=fCol[i*3+1]=fCol[i*3+2]=0;continue;}
    const a=centers[flow.from[i]],b=centers[flow.to[i]];
    const p=bez([a.x,a.y,a.z],[flow.mid[i*3],flow.mid[i*3+1],flow.mid[i*3+2]],[b.x,b.y,b.z],flow.t[i]);
    fPos[i*3]=p[0];fPos[i*3+1]=p[1];fPos[i*3+2]=p[2];
    const c=SUBS_COLOR[flow.from[i]].clone().lerp(WHITE,0.3);
    const fade=Math.sin(Math.PI*Math.min(1,flow.t[i]));
    fCol[i*3]=c.r*fade;fCol[i*3+1]=c.g*fade;fCol[i*3+2]=c.b*fade;
  }
  fGeo.attributes.position.needsUpdate=true;
  fGeo.attributes.color.needsUpdate=true;
  renderer.render(scene,camera);
}
tick();

// ===== 图例 =====
{
  const lg = document.getElementById("legend");
  SUBS.forEach(s => {
    const el = document.createElement("span");
    el.innerHTML = '<i style="background:'+s[1]+';color:'+s[1]+'"></i>'+s[0];
    lg.appendChild(el);
  });
}

// ===== 思考流 =====
const FILTERS = ["全部","对话","动作","反思","监管","情感"];
const CHIP_TYPES = {"对话":["dialogue"],"动作":["action"],"反思":["reflection"],"监管":["thread","coverage","experiment","selection"],"情感":["dialogue"]};
const TYPE_LABEL = {action:"动作",reflection:"反思",dialogue:"对话",thread:"线程",coverage:"覆盖",experiment:"实验",selection:"选择",error:"错误"};
const stream = document.getElementById("stream");
const known = new Set();
const colorsBySub = Object.fromEntries(SUBS.map(s=>[s[0],s[1]]));
let filter = "全部";

function renderEvent(e) {
  if (known.has(e.id)) return;
  known.add(e.id);
  const div = document.createElement("div");
  div.className = "event flash";
  div.dataset.types = e.type;
  const subs = (e.subsystems||[]).map(name =>
    '<span class="subs" style="color:'+colorsBySub[name]+';border-color:'+colorsBySub[name]+'88">'+name+'</span>').join("");
  div.innerHTML = '<div class="meta">cycle ' + (e.cycle === null ? "—" : Math.round(e.cycle)) + ' · ' + (TYPE_LABEL[e.type]||e.type) + subs + '</div>' + escapeHtml(e.text);
  stream.prepend(div);
  while (stream.children.length > 240) stream.removeChild(stream.lastChild);
  if (document.getElementById("autoscroll").checked) stream.scrollTop = 0;
  setTimeout(()=>div.classList.remove("flash"), 1200);
}
function escapeHtml(s) { const d=document.createElement("div"); d.textContent=s; return d.innerHTML; }
function applyFilter() {
  const types = CHIP_TYPES[filter] || null;
  [...stream.children].forEach(div => {
    div.style.display = (!types || types.includes(div.dataset.types)) ? "" : "none";
  });
}
{
  const chips = document.getElementById("chips");
  FILTERS.forEach(name => {
    const c = document.createElement("span");
    c.className = "chip" + (name==="全部" ? " active" : "");
    c.textContent = name;
    c.onclick = () => {
      filter = name;
      [...chips.children].forEach(x => x.classList.toggle("active", x.textContent === name));
      applyFilter();
    };
    chips.appendChild(c);
  });
}

// ===== 轮询 =====
let lastSeq = 0;
async function poll() {
  try {
    const resp = await fetch("/api/digest?since=" + lastSeq, {cache: "no-store"});
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    const d = await resp.json();
    document.getElementById("reconnect").hidden = true;
    document.getElementById("hud-status").textContent = d.status;
    document.getElementById("hud-cycle").textContent = d.cycle;
    document.getElementById("hud-errors").textContent = d.consecutive_errors;
    document.getElementById("hud-action").textContent = d.last_action || "—";
    document.getElementById("hud-affect").textContent =
      "V" + d.affect.valence.toFixed(2) + " A" + d.affect.arousal.toFixed(2) + " T" + d.affect.threat.toFixed(2);
    setActivation(d.activations);
    d.events.forEach(renderEvent);
    if (d.events.length) applyFilter();
    lastSeq = Math.max(lastSeq, d.seq);
  } catch (err) {
    document.getElementById("reconnect").hidden = false;
  }
}
poll();
setInterval(poll, 1500);
```

（`brain-style-v4.html` 中的视觉参数如需微调（光晕强度、粒子密度），直接改本文件顶部的常量即可，不涉及 digest。）

- [ ] **Step 4: 本地静态验证**

Run: `C:\Users\JBoon\AppData\Local\Programs\Python\Python311\python.exe scripts/dashboard.py --state-dir state --port 8765`
然后浏览器打开 `http://127.0.0.1:8765`，人工确认：布局正常、HUD 出现、大脑渲染、思考流出现事件、过滤 chips 可切换。（此时 three.js 尚未 vendor，控制台会报错——属于 Task 8 的预期。）

- [ ] **Step 5: Commit**

```bash
git add src/agent_runtime/dashboard/static/index.html src/agent_runtime/dashboard/static/style.css src/agent_runtime/dashboard/static/app.js
git commit -m "feat: add dashboard frontend with brain renderer and event stream"
```

---

### Task 8: vendor three.js（本地化，不依赖 CDN）

**Files:**
- Create: `src/agent_runtime/dashboard/static/vendor/three.min.js`

- [ ] **Step 1: 下载并校验**

```bash
mkdir -p /d/agent-runtime/src/agent_runtime/dashboard/static/vendor
curl -fsSL -o /d/agent-runtime/src/agent_runtime/dashboard/static/vendor/three.min.js https://cdn.bootcdn.net/ajax/libs/three.js/0.160.0/three.min.js
ls -la /d/agent-runtime/src/agent_runtime/dashboard/static/vendor/three.min.js
head -c 120 /d/agent-runtime/src/agent_runtime/dashboard/static/vendor/three.min.js
```

Expected: 文件存在且约 640KB；开头为 three.js 的 banner 注释。
若 bootcdn 不可达，换用备用源：`https://registry.npmmirror.com/three/0.160.0/files/build/three.min.js`。
两个源都失败则停止并报告（不写占位文件）。

- [ ] **Step 2: 端到端人工验证**

启动 `scripts/dashboard.py`（同 Task 7 Step 4），浏览器打开页面，确认大脑渲染成功、可拖动、信息流动画存在、控制台无 404。

- [ ] **Step 3: Commit**

```bash
git add src/agent_runtime/dashboard/static/vendor/three.min.js
git commit -m "chore: vendor three.js for offline dashboard rendering"
```

---

### Task 9: 真实状态冒烟测试 + 全量回归 + 文档

**Files:**
- Modify: `docs/PROGRESS.md`（新增仪表盘一节）
- 无源码修改

- [ ] **Step 1: 全量回归**

Run: `C:\Users\JBoon\AppData\Local\Programs\Python\Python311\python.exe -m pytest -q`
Expected: 全绿（此前基线 408 + 本计划新增约 10 = ~418 passed）

- [ ] **Step 2: 真实状态冒烟（runner 运行中，零干扰验证）**

```bash
C:\Users\JBoon\AppData\Local\Programs\Python\Python311\python.exe scripts/dashboard.py --state-dir state --port 8765 &
curl -s http://127.0.0.1:8765/api/digest | head -c 600
```

Expected: digest 返回 `agent_d3a6402bf7324690`、真实 cycle；页面打开后 HUD 与思考流随 runner 更新。
随后确认 runner 未受影响：`runtime_status.json` 连续错误仍为 0、状态 RUNNING；`state/` 无新文件（仪表盘零写入）。

- [ ] **Step 3: 更新 PROGRESS.md**

在 `docs/PROGRESS.md` 的"如何自查进度"一节后追加：

```markdown
## 实时仪表盘

- 启动：`python scripts/dashboard.py --state-dir state`（默认 http://127.0.0.1:8765，只读，与 runner 隔离）
- 3D 大脑：9 个功能子系统星团 + 信息流，拖动/缩放；思考流：全事件 + 过滤
- 规格：docs/superpowers/specs/2026-08-24-agent-dashboard-design.md
```

- [ ] **Step 4: 更新记忆并汇报**

更新 `C:\Users\JBoon\.claude\projects\D--agent-runtime\memory\agent-continuation-loop.md`（仪表盘入口与命令一行），并向用户汇报完成状态（含页面 URL 与使用方式）。

- [ ] **Step 5: Commit**

```bash
git add docs/PROGRESS.md
git commit -m "docs: add dashboard section to progress board"
```

---

## 自检记录

- **规格覆盖**：§3 架构（Task 1-6）、§3.1 API（Task 3-5）、§3.2 激活映射（Task 2）、§3.3 信息流（Task 7）、§4 布局（Task 7）、§5 错误处理（Task 1 UNKNOWN + Task 7 重连）、§6 测试（Task 1-6, 9）、§7 范围外无任务、§8 完成标准（Task 9）。
- **占位符扫描**：无 TBD/TODO；每步含完整代码或精确命令与期望输出。
- **类型一致性**：digest 返回键 `activations/pulses/events/seq` 在 Task 1-5 与 app.js 消费处一致；`SUBSYSTEMS` 顺序与 app.js 的 `SUBS` 顺序一致（索引即连线图索引）。
