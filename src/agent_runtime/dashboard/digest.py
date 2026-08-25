"""Read-only digest of Agent state for the dashboard. Never writes state."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
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


def _in_time_window_str(value: Any, now: datetime, seconds: int) -> bool:
    dt = _parse_dt(value)
    return dt is not None and now - timedelta(seconds=seconds) <= dt <= now


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


def _agency_signals(data: dict, cycle: int, window_cycles: int) -> int:
    total = 0
    for thread in _unwrap_list(data.get("agency_threads")):
        total += _count_cycles(
            _unwrap_list(_unwrap(thread).get("events")), "cycle", cycle, window_cycles
        )
    for probe in _unwrap_list(data.get("diagnostic_coverage_probes")):
        p = _unwrap(probe)
        total += _count_cycles(
            _unwrap_list(p.get("influences")), "cycle", cycle, window_cycles
        )
        if isinstance(p.get("cycle"), int) and cycle - window_cycles < p.get("cycle") <= cycle:
            total += 1
    total += _count_cycles(
        _unwrap_list(data.get("selection_traces")), "cycle", cycle, window_cycles
    )
    total += _count_cycles(
        _unwrap_list(data.get("agency_experiments")),
        "scheduled_cycle",
        cycle,
        window_cycles,
    )
    return total


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


def _read_journal_thoughts(state_dir: Path, limit: int = 10) -> list[dict]:
    files = sorted(Path(state_dir).glob("archive/lifecycle/lifecycle-*.jsonl"))
    if not files:
        return []
    entries: list[dict] = []
    for line in files[-1].read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("reflection"):
            entries.append(entry)
    return entries[-limit:]


def _collect_events(
    state_dir: Path, data: dict, status: dict, now: datetime
) -> list[dict]:
    cycle = int(status.get("current_cycle") or 0)
    records = _unwrap(data.get("records")) or {}
    outcome_by_action = {
        _unwrap(o).get("action_id"): _unwrap(o)
        for o in _unwrap_list(records.get("outcomes"))[-400:]
    }
    events: list[dict] = []
    for a in _unwrap_list(records.get("actions"))[-60:]:
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
    for r in _unwrap_list(records.get("reflections"))[-40:]:
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
                "text": (
                    f"覆盖 {pp.get('target_path')} · {ii.get('authorization_result')} "
                    f"(probe {pp.get('probe_id')})"
                ),
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
    for t in _unwrap_list(data.get("selection_traces"))[-40:]:
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
    for j in _read_journal_thoughts(state_dir):
        events.append({
            "id": j.get("reflection_id") or f"journal-{j.get('cycle')}",
            "type": "thought",
            "cycle": j.get("cycle"),
            "ts": j.get("recorded_at"),
            "text": str(j.get("reflection")),
            "subsystems": ["自我模型"],
        })
    ranks = {
        "action": 0, "reflection": 1, "dialogue": 2, "thread": 3,
        "coverage": 4, "experiment": 5, "selection": 6, "error": 7,
        "thought": 8,
    }
    events.sort(
        key=lambda e: (e["cycle"] or cycle, ranks.get(e["type"], 9), str(e["id"]))
    )
    keep_types = {"thought", "dialogue"}  # 思考/对话永不被截断
    kept = [e for e in events if e["type"] in keep_types]
    regular = [e for e in events if e["type"] not in keep_types][-180:]
    events = sorted(regular + kept, key=lambda e: (e["cycle"] or cycle, str(e["id"])))
    for index, event in enumerate(events):
        event["seq"] = index + 1  # 1-based：首次拉取 since=0 拿到全部
    return events


def _collect_activations(
    state_dir: Path, data: dict, cycle: int, now: datetime, window_cycles: int
) -> dict[str, float]:
    records = _unwrap(data.get("records")) or {}
    seconds = window_cycles * 60
    narrative = []
    auto_memory = _unwrap(data.get("autobiographical_memory"))
    if isinstance(auto_memory, dict):
        narrative = _unwrap_list(auto_memory.get("self_narrative"))
    signals: dict[str, int] = {
        "认知": _count_cycles(
            _unwrap_list(records.get("decisions")), "cycle", cycle, window_cycles
        ),
        "感知": _count_cycles(
            _unwrap_list(records.get("observations")), "cycle", cycle, window_cycles
        ),
        "行动": _count_cycles(
            _unwrap_list(records.get("actions")), "cycle", cycle, window_cycles
        ),
        "动机/存活": _count_cycles(
            _unwrap_list(records.get("goals_created")), "cycle", cycle, window_cycles
        )
        + _count_cycles(
            _unwrap_list(records.get("motivations")), "cycle", cycle, window_cycles
        ),
        "自我模型": _count_cycles(
            _unwrap_list(records.get("reflections")), "cycle", cycle, window_cycles
        ),
        "记忆": _count_time(narrative, "occurred_at", now, seconds),
        "学习": _count_time(
            _unwrap_list(data.get("verified_learning_evidence")),
            "verified_at",
            now,
            seconds,
        ),
        "情感/社会": _count_dialogue(state_dir, now, seconds),
        "监管": _agency_signals(data, cycle, window_cycles),
    }
    return {name: round(min(1.0, count / 10.0), 3) for name, count in signals.items()}


def build_digest(state_dir: Path, since: int = 0, window_cycles: int = 30) -> dict:
    state_dir = Path(state_dir)
    status = _load_json(state_dir / "runtime_status.json")
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
    cycle = int(status.get("current_cycle") or 0)
    state = _load_json(state_dir / "agent_state.json")
    data = state.get("data", state) if isinstance(state, dict) else {}
    now = datetime.now(timezone.utc)
    return {
        "agent_id": status.get("agent_id"),
        "status": status.get("status"),
        "cycle": cycle,
        "consecutive_errors": int(status.get("consecutive_errors") or 0),
        "last_action": status.get("last_action"),
        "affect": {
            "valence": float((status.get("affect") or {}).get("valence") or 0.0),
            "arousal": float((status.get("affect") or {}).get("arousal") or 0.0),
            "threat": float((status.get("affect") or {}).get("threat") or 0.0),
        },
        "activations": _collect_activations(
            state_dir, data, cycle, now, window_cycles
        ),
        "pulses": _collect_pulses(state_dir, data, status, now, since),
        "events": _collect_incremental_events(
            state_dir, data, status, now, since
        ),
        "seq": _latest_seq(state_dir, data, status, now),
    }


def _collect_incremental_events(
    state_dir: Path, data: dict, status: dict, now: datetime, since: int
) -> list[dict]:
    return [
        e
        for e in _collect_events(state_dir, data, status, now)
        if e["seq"] > since
    ]


def _collect_pulses(
    state_dir: Path, data: dict, status: dict, now: datetime, since: int
) -> list[str]:
    pulses: list[str] = []
    for event in _collect_incremental_events(state_dir, data, status, now, since):
        for name in event["subsystems"]:
            if name not in pulses:
                pulses.append(name)
    return pulses


def _latest_seq(state_dir: Path, data: dict, status: dict, now: datetime) -> int:
    return max(
        (e["seq"] for e in _collect_events(state_dir, data, status, now)),
        default=0,
    )
