"""Read-only production backup and bounded real-data contract audit.

Only the validation directory is written. No production constructor, models,
browser interaction, external action, service switch or adoption is invoked.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import time


BASE = Path("/home/ubuntu/validation/memory-graph-20261005")
SOURCE = Path("/home/ubuntu/content-agent-state")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--sample-limit", type=int, default=12)
    args = parser.parse_args()
    if os.geteuid() == 0 or Path(sys.prefix).resolve() != (BASE / "venv").resolve():
        raise RuntimeError("use the regular owner's separate validation environment")
    if args.source_root.resolve() != SOURCE.resolve() or not 1 <= args.sample_limit <= 100:
        raise ValueError("only the known production source and bounded audit sample are accepted")
    production = args.source_root / "application/memory_system"
    run = BASE / "production-snapshots" / ("run-" + str(time.time_ns()))
    run.mkdir(parents=True, mode=0o700)
    destination = run / "state/application/memory_system"
    destination.mkdir(parents=True, mode=0o700)
    receipt = {"run_dir": str(run), "production_modified": False, "production_deployed": False,
               "full_memory_plan_complete": False, "sample_limit": args.sample_limit,
               "claim": "contract audit only; not full retrieval/learning/business acceptance"}
    try:
        with sqlite3.connect((production / "memory.db").as_uri() + "?mode=ro", uri=True) as source:
            with sqlite3.connect(destination / "memory.db") as target:
                source.backup(target, pages=256, sleep=.01)
                check = target.execute("PRAGMA quick_check").fetchone()[0]
                if check != "ok":
                    raise RuntimeError("snapshot integrity check failed")
        receipt["backup_database_bytes"] = (destination / "memory.db").stat().st_size
        archive_files, archive_bytes = 0, 0
        for directory in ("evidence", "evidence-v2"):
            origin = production / directory
            if not origin.exists():
                continue
            for path in origin.rglob("*"):
                if not path.is_file():
                    continue
                relative = path.resolve().relative_to(origin.resolve())
                copied = destination / directory / relative
                copied.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, copied)
                archive_files += 1
                archive_bytes += copied.stat().st_size
        receipt["archive_backup"] = {"files": archive_files, "bytes": archive_bytes}
        # Constructors below act only on the private SQLite backup.
        from agent_runtime.memory.durable import CanonicalMemoryStore
        from agent_runtime.memory.archive import EvidenceArchive
        from agent_runtime.memory.documents import DocumentHierarchy
        from agent_runtime.memory.projection_scope import ProjectionScope
        from agent_runtime.memory.retrieval import fingerprint
        from agent_runtime.memory.revisions import digest, now
        store = CanonicalMemoryStore(run / "state")
        with store._connect() as db:
            receipt["canonical_counts"] = {table: db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
                for table in ("memories", "memory_revisions", "memory_adoptions")}
            receipt["scopes"] = [dict(row) for row in db.execute("""SELECT tenant_id,owner_id,agent_id,access_scope,
                COUNT(*) AS revisions FROM memory_revisions GROUP BY tenant_id,owner_id,agent_id,access_scope
                ORDER BY revisions DESC""")]
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            legacy = [dict(row) for row in db.execute("SELECT * FROM evidence_sources ORDER BY source_id")] if "evidence_sources" in tables else []
        # Legacy pointers cannot prove access scope; report that migration input.
        archive_audit = []
        for row in legacy:
            archive = Path(row["archive"])
            relative = archive.resolve().relative_to((production / "evidence").resolve())
            body = json.loads((destination / "evidence" / relative).read_text(encoding="utf-8"))
            observed = str(body.get("evidence_text") or body.get("summary") or "")
            if not observed or fingerprint(observed) != row["source_version"]:
                raise RuntimeError("legacy archive version does not match its exact observed text")
            archive_audit.append({"source_id_hash": digest(row["source_id"]), "characters": len(observed),
                "representation": "OBSERVED_EXCERPT" if body.get("evidence_text") else "SUMMARY_ONLY",
                "source_version": row["source_version"], "scope": "NOT_PRESENT_IN_LEGACY_POINTER"})
        receipt["legacy_archive_audit"] = {"checked": len(archive_audit),
            "summary_only": sum(r["representation"] == "SUMMARY_ONLY" for r in archive_audit),
            "requires_explicit_scope_migration": bool(archive_audit)}
        (run / "archive-audit.json").write_text(json.dumps(archive_audit, indent=2) + "\n")
        samples = []
        for row in receipt["scopes"]:
            if len(samples) == args.sample_limit:
                break
            scope = ProjectionScope(**{k: row[k] for k in ("tenant_id", "owner_id", "agent_id", "access_scope")})
            records = store.query_claims(**asdict(scope), valid_at=now(), statuses=["ACTIVE"],
                                         limit=args.sample_limit - len(samples))
            parser = DocumentHierarchy(store, scope, chunk_sizes=(512, 128), overlap=16)
            archive = EvidenceArchive(store, scope)
            for record in records:
                original = archive.content(record)
                document = parser.parse(record["claim_id"], original, source_version=record["revision_id"])
                for chunk in document["chunks"]:
                    span = chunk["source_spans"][0]
                    if original[span["start"]:span["end"]] != chunk["body"]:
                        raise RuntimeError("real-data document span changed exact text")
                reopened = DocumentHierarchy(store, scope, chunk_sizes=(512, 128), overlap=16)
                if reopened.load(record["claim_id"], record["revision_id"]) != document:
                    raise RuntimeError("real-data parsing artifact failed recovery")
                samples.append({"claim_id_hash": digest(record["claim_id"]), "scope": asdict(scope),
                    "kind": record["kind"], "characters": len(original), "chunks": len(document["chunks"]),
                    "content_hash": digest(original), "archive_present": bool(record["payload"].get("evidence_archive")),
                    "representation": "ARCHIVED_SOURCE" if record["payload"].get("evidence_archive") else "CANONICAL_RECORD"})
        if not samples:
            raise RuntimeError("no active production records available for bounded audit")
        receipt.update(status="PASSED", audited_samples=len(samples), samples=samples)
    except Exception as exc:
        receipt.update(status="FAILED", error_type=type(exc).__name__, error=str(exc))
        raise
    finally:
        (run / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
        print(json.dumps({k: v for k, v in receipt.items() if k != "samples"}))


if __name__ == "__main__":
    main()
