"""The snapshot auditor never constructs or modifies the production store."""
from dataclasses import asdict
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

from agent_runtime.memory.archive import EvidenceArchive
from agent_runtime.memory.durable import CanonicalMemoryStore
from test_topic_hierarchy import SCOPE


def test_record_subject_is_not_used_as_default_agent_scope(tmp_path):
    store = CanonicalMemoryStore(tmp_path, default_subject_id="ferro")
    store.propose_revision({"claim_id": "belief", "revision_id": "belief-v1", "subject_id": "research-belief:btc",
        "kind": "SOURCE_CLAIM", "text": "A sourced research belief", "recorded_at": "2025-01-01T00:00:00Z"})
    store.record_adoption({"decision_id": "belief-d1", "revision_id": "belief-v1", "status": "ACTIVE", "reason": "test"})
    record = store.read_claim("belief", agent_id="ferro")
    assert record is not None and record["subject_id"] == "research-belief:btc"
    assert record["agent_id"] == "ferro"
    assert store.read_claim("belief", agent_id="research-belief:btc") is None


def test_private_snapshot_preserves_original_database_and_archive(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[1] / "scripts/validate_memory_snapshot.py"
    spec = importlib.util.spec_from_file_location("snapshot_validator", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.BASE, module.SOURCE = tmp_path / "validation", tmp_path / "production"
    (module.BASE / "venv").mkdir(parents=True)
    production = CanonicalMemoryStore(module.SOURCE)
    EvidenceArchive(production, SCOPE).propose(claim_id="source", revision_id="r1",
        body="精确原文  保留空格。\n保留换行。", record={"text": "navigation title", "recorded_at": "2025-01-01T00:00:00Z"})
    production.record_adoption({"decision_id": "d1", "revision_id": "r1", "status": "ACTIVE", "reason": "observed archive"})
    def fingerprints():
        return {str(p.relative_to(production.root)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in production.root.rglob("*") if p.is_file()}
    before = fingerprints()
    monkeypatch.setattr(module.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(sys, "prefix", str(module.BASE / "venv"))
    monkeypatch.setattr(sys, "argv", [str(path), "--source-root", str(module.SOURCE), "--sample-limit", "1"])
    module.main()
    assert before == fingerprints()
    run = next((module.BASE / "production-snapshots").glob("run-*"))
    receipt = json.loads((run / "receipt.json").read_text())
    assert receipt["status"] == "PASSED" and receipt["audited_samples"] == 1
    assert not receipt["production_modified"] and not receipt["full_memory_plan_complete"]
    assert receipt["samples"][0]["archive_present"]
    assert receipt["samples"][0]["scope"] == asdict(SCOPE)
    assert receipt["archive_backup"]["files"] == 1
