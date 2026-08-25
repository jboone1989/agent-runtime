"""Capture and compare real-time longitudinal Agent Runtime evidence."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_runtime.runtime import AgentRuntime
from agent_runtime.service import read_runtime_status


def snapshot(state_dir: Path) -> dict[str, Any]:
    runtime = AgentRuntime.restore(state_dir)
    status = read_runtime_status(state_dir) or {}
    lifecycle = state_dir / "archive" / "lifecycle"
    lifecycle_files = sorted(lifecycle.glob("lifecycle-*.jsonl"))
    relationships = runtime.autobiographical_memory.relationships
    tendencies = runtime.value_formation.context(limit=16)
    affect = runtime.social_affect.context()
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "agent_id": runtime.identity.agent_id,
        "genesis_id": runtime.identity.genesis_id,
        "cycle": runtime.cycle,
        "runner": {
            "status": status.get("status"),
            "process_id": status.get("process_id"),
            "started_at": status.get("started_at"),
            "ticks_completed": status.get("ticks_completed", 0),
            "consecutive_errors": status.get("consecutive_errors", 0),
            "reflection_errors": status.get("reflection_errors", 0),
            "reflections_completed": status.get("reflections_completed", 0),
        },
        "experience": {
            "episodes": len(runtime.memory.episodic.episodes),
            "actions": len(runtime.records.actions),
            "outcomes": len(runtime.records.outcomes),
            "reflections": len(runtime.records.reflections),
            "experience_traces": len(runtime.experience_traces),
            "dialogue_exchanges": len(runtime.autobiographical_memory.dialogue_index),
            "self_narratives": len(runtime.autobiographical_memory.self_narrative),
            "relationship_partners": len(relationships),
            "relationship_interactions": sum(
                item.interaction_count for item in relationships.values()
            ),
        },
        "agency": {
            "active_goals": len(runtime.active_goals),
            "all_goals": len(runtime.goals),
            "last_action": runtime.next_action_kind,
            "behavioral_tendencies": tendencies,
            "functional_affect": affect,
        },
        "archives": {
            "lifecycle_files": len(lifecycle_files),
            "lifecycle_bytes": sum(path.stat().st_size for path in lifecycle_files),
            "runtime_history_exists": (state_dir / "archive" / "runtime").exists(),
        },
    }


def compare(baseline: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    started = datetime.fromisoformat(str(baseline["captured_at"]))
    ended = datetime.fromisoformat(str(current["captured_at"]))
    elapsed_hours = (ended - started).total_seconds() / 3600.0
    experience_delta = {
        key: int(current["experience"][key]) - int(baseline["experience"][key])
        for key in baseline["experience"]
    }
    return {
        "same_identity": current["agent_id"] == baseline["agent_id"],
        "same_genesis": current["genesis_id"] == baseline["genesis_id"],
        "elapsed_hours": round(elapsed_hours, 4),
        "cycle_delta": int(current["cycle"]) - int(baseline["cycle"]),
        "experience_delta": experience_delta,
        "runner_status": current["runner"]["status"],
        "consecutive_errors": current["runner"]["consecutive_errors"],
        "reflection_errors": current["runner"]["reflection_errors"],
        "milestones": {
            "24h_ready": elapsed_hours >= 24.0,
            "72h_ready": elapsed_hours >= 72.0,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--capture", action="store_true")
    args = parser.parse_args()
    state_dir = args.state_dir.resolve()
    baseline_path = (args.baseline or state_dir / "longitudinal_baseline.json").resolve()
    current = snapshot(state_dir)
    if args.capture:
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(
            json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        result = {"baseline_path": str(baseline_path), "baseline": current}
    else:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        result = {
            "baseline_path": str(baseline_path),
            "current": current,
            "comparison": compare(baseline, current),
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
