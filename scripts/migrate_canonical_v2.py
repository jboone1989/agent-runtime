"""Migrate canonical state with its writers paused, preserving an exact backup."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import resource
import shutil
import sqlite3
import subprocess
import sys
import time

from verify_canonical_v2 import data_baseline, file_hash


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--backup-root", type=Path, required=True)
    parser.add_argument("--paused-unit", required=True)
    args = parser.parse_args()
    active = subprocess.run(["systemctl", "is-active", args.paused_unit], text=True, capture_output=True)
    if active.stdout.strip() != "inactive":
        raise RuntimeError("canonical writers must be paused before migration")
    state = args.state_root.resolve(strict=True)
    backup = args.backup_root.resolve()
    if backup.is_relative_to(state) or state.is_relative_to(backup):
        raise ValueError("backup must be separate from canonical state")
    backup.mkdir(parents=True, exist_ok=False)
    source = state / "application/memory_system/memory.db"
    destination = backup / "application/memory_system/memory.db"
    destination.parent.mkdir(parents=True)
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as live:
        with sqlite3.connect(destination) as saved:
            live.backup(saved, pages=256)
    before = data_baseline(destination)
    archives = []
    for path in sorted((source.parent / "evidence").rglob("*")):
        if path.is_file():
            copied = destination.parent / path.relative_to(source.parent)
            copied.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, copied)
            sha256 = file_hash(copied)
            assert sha256 == file_hash(path), "evidence changed during backup"
            archives.append({"path": str(path.relative_to(source.parent)), "sha256": sha256})
    candidate = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(candidate / "src"))
    import agent_runtime.memory.durable as durable
    assert Path(durable.__file__).resolve().is_relative_to(candidate)
    started = time.monotonic()
    store = durable.CanonicalMemoryStore(state)
    elapsed = time.monotonic() - started
    after = data_baseline(source)
    assert before == after, "migration changed canonical v1 rows"
    with store._connect() as db:
        version = db.execute("SELECT value FROM schema_meta WHERE key='canonical_contract_version'").fetchone()[0]
        counts = {table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ("memory_revisions", "memory_adoptions")}
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
    assert version == "2" and integrity == "ok"
    commit = subprocess.check_output(["git", "-C", str(candidate), "rev-parse", "HEAD"], text=True).strip()
    receipt = {"schema": "canonical-v2.production-migration.v1", "commit": commit,
               "candidate_module": durable.__file__, "state_root": str(state),
               "backup_root": str(backup), "paused_unit": args.paused_unit,
               "before": before, "after": after, "legacy_parity": before == after,
               "contract_version": int(version), "counts": counts,
               "archives": archives, "integrity_check": integrity,
               "migration_seconds": round(elapsed, 3),
               "max_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
    receipt_path = backup / "migration-receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({k: v for k, v in receipt.items() if k not in ("archives", "before", "after")}))
    print(json.dumps({"receipt_path": str(receipt_path)}))


if __name__ == "__main__":
    main()
