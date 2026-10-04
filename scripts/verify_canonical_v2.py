"""Verify canonical-v2 on an online read-only snapshot, never on live state.

Also capture the production release/import baseline without changing services.
The output directory must not exist so a previous verification is never replaced.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import resource
import shutil
import sqlite3
import subprocess
import sys
import time


def emit(value):
    print(json.dumps(value, sort_keys=True, ensure_ascii=False))


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def data_baseline(path):
    tables = {}
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
        for table in ("memories", "memory_events", "memory_entities", "memory_topics"):
            digest = hashlib.sha256()
            count = 0
            # Existing tables are retained byte-for-byte at the row level.
            for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid"):
                digest.update(json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode())
                digest.update(b"\n")
                count += 1
            tables[table] = {"count": count, "row_sha256": digest.hexdigest()}
    return tables


def production_baseline():
    home = Path.home()
    runtime = (home / "agent-runtime/current").resolve(strict=True)
    ferro = (home / "content-agent/current").resolve(strict=True)
    releases = {}
    for name, path in (("runtime", runtime), ("ferro", ferro)):
        commit = subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()
        dirty = subprocess.check_output(["git", "-C", str(path), "status", "--porcelain", "--untracked-files=all"], text=True).strip()
        releases[name] = {"path": str(path), "commit": commit, "clean": not dirty, "changes": dirty.splitlines()}
    pid = subprocess.check_output(["systemctl", "show", "content-agent.service", "--value", "-p", "MainPID"], text=True).strip()
    process = Path("/proc") / pid
    command = process.joinpath("cmdline").read_bytes().decode().strip("\0").split("\0")
    environment = dict(item.split("=", 1) for item in process.joinpath("environ").read_bytes().decode().split("\0") if "=" in item)
    probe = subprocess.run([command[0], str(Path(__file__).resolve()), "--probe-production-imports"], env=environment, text=True, capture_output=True)
    if probe.returncode:
        raise RuntimeError("production import probe failed: " + probe.stderr)
    imports = json.loads(probe.stdout)
    releases["process"] = {"pid": int(pid), "command": command, "pythonpath": environment.get("PYTHONPATH", "")}
    releases["import_probe"] = imports
    releases["probe_scope"] = "Fresh process using the running service interpreter, environment and persistent .pth files; live process module cache is not inspected."
    releases["consistent"] = (
        releases["runtime"]["clean"] and releases["ferro"]["clean"]
        and Path(imports["runtime_file"]).is_relative_to(runtime)
        and Path(imports["ferro_file"]).is_relative_to(ferro)
        and Path(command[1]).is_relative_to(ferro)
    )
    return releases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--probe-production-imports", action="store_true")
    args = parser.parse_args()
    if args.probe_production_imports:
        import agent_runtime.memory.durable as durable
        import wechat_traffic_agent.memory_system as ferro
        emit({"runtime_file": str(Path(durable.__file__).resolve()), "ferro_file": str(Path(ferro.__file__).resolve())})
        return
    if args.state_root is None or args.output_root is None:
        parser.error("--state-root and --output-root are required")
    state_root = args.state_root.resolve(strict=True)
    output = args.output_root.resolve()
    if output.is_relative_to(state_root) or state_root.is_relative_to(output):
        raise ValueError("verification output must be separate from production state")
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    production = production_baseline()
    source = state_root / "application/memory_system/memory.db"
    snapshot_root = output / "snapshot"
    snapshot_path = snapshot_root / "application/memory_system/memory.db"
    snapshot_path.parent.mkdir(parents=True)
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as live:
        with sqlite3.connect(snapshot_path) as snapshot:
            live.backup(snapshot, pages=256)
    before = data_baseline(snapshot_path)
    backup_path = output / "backup/application/memory_system/memory.db"
    backup_path.parent.mkdir(parents=True)
    shutil.copyfile(snapshot_path, backup_path)
    archives = []
    archive_root = source.parent / "evidence"
    if not archive_root.exists():
        archive_root = source.parent / "sources"
    for path in sorted(archive_root.rglob("*")):
        if path.is_file():
            relative = path.relative_to(source.parent)
            copied = backup_path.parent / relative
            copied.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, copied)
            sha256 = file_hash(copied)
            assert sha256 == file_hash(path), "archive changed during backup"
            archives.append({"path": str(relative), "bytes": copied.stat().st_size, "sha256": sha256})
    # Use the candidate code explicitly; never allow production .pth precedence
    # to turn the verification into a test of the old adapter.
    candidate = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(candidate / "src"))
    from agent_runtime.memory.durable import CanonicalMemoryStore
    migrate_start = time.monotonic()
    store = CanonicalMemoryStore(snapshot_root)
    migration_seconds = time.monotonic() - migrate_start
    after = data_baseline(snapshot_path)
    assert before == after, "legacy rows changed during migration"
    with store._connect() as db:
        revision_count = db.execute("SELECT COUNT(*) FROM memory_revisions").fetchone()[0]
        adoption_count = db.execute("SELECT COUNT(*) FROM memory_adoptions").fetchone()[0]
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
    assert revision_count == adoption_count == before["memories"]["count"]
    reopen_start = time.monotonic()
    reopened = CanonicalMemoryStore(snapshot_root)
    reopen_seconds = time.monotonic() - reopen_start
    assert reopened.query_claims(limit=1)
    receipt = {
        "schema": "canonical-v2.snapshot-verification.v1", "production": production,
        "before": before, "after": after, "legacy_parity": before == after,
        "revision_count": revision_count, "adoption_count": adoption_count,
        "archive": archives, "archive_files": len(archives), "integrity_check": integrity,
        "migration_seconds": round(migration_seconds, 3), "reopen_seconds": round(reopen_seconds, 3),
        "total_seconds": round(time.monotonic() - started, 3),
        "max_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "candidate_path": str(candidate), "production_changed": False,
        "canonical_backup": str(backup_path.parent),
    }
    receipt_path = output / "receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n")
    emit({k: v for k, v in receipt.items() if k not in ("archive", "before", "after")})
    emit({"receipt_path": str(receipt_path)})


if __name__ == "__main__":
    main()
