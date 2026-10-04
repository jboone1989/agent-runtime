"""Optional local semantic-retrieval provider.

This module intentionally keeps FastEmbed, NumPy and hnswlib behind lazy imports.
Agent Runtime core does not require these packages. Agents that choose this local
provider get the existing short-lived model worker and incremental HNSW index.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import time
from typing import Any
from uuid import uuid4


_DEFAULT_EMBEDDING = (
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
)
_DEFAULT_RERANKER = "ferro/bge-reranker-base-int8"
_DEFAULT_CACHE = str(Path.home() / ".cache" / "ferro-memory-models")


def _env(primary: str, legacy: str, default: str) -> str:
    return os.getenv(primary) or os.getenv(legacy) or default


class LocalRetrievalModels:
    """CPU embedding/reranking provider with bounded transient workers."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.embedding = _env(
            "AGENT_MEMORY_EMBEDDING",
            "FERRO_MEMORY_EMBEDDING",
            _DEFAULT_EMBEDDING,
        )
        self.reranker = _env(
            "AGENT_MEMORY_RERANKER",
            "FERRO_MEMORY_RERANKER",
            _DEFAULT_RERANKER,
        )
        self.model_id = self.embedding + ":normalized:v1"
        self.cache = _env(
            "AGENT_MEMORY_MODEL_CACHE",
            "FERRO_MEMORY_MODEL_CACHE",
            _DEFAULT_CACHE,
        )

    def _run(
        self,
        operation: str,
        texts: list[str],
        query: str = "",
    ) -> Any:
        request = {
            "operation": operation,
            "texts": texts,
            "query": query,
            "embedding": self.embedding,
            "reranker": self.reranker,
            "cache": self.cache,
        }
        cache = Path(self.cache)
        cache.mkdir(parents=True, exist_ok=True)
        environment = dict(os.environ)
        user_runtime = "/run/user/" + str(os.getuid())
        environment.setdefault("XDG_RUNTIME_DIR", user_runtime)
        environment.setdefault(
            "DBUS_SESSION_BUS_ADDRESS",
            "unix:path=" + user_runtime + "/bus",
        )
        with (cache / "inference.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            process = subprocess.run(
                [
                    "systemd-run",
                    "--user",
                    "--quiet",
                    "--pipe",
                    "--wait",
                    "--collect",
                    "--property=RuntimeMaxSec=240",
                    "--property=MemoryMax=1G",
                    "--property=MemorySwapMax=0",
                    "--unit=agent-memory-" + uuid4().hex,
                    sys.executable,
                    str(Path(__file__).resolve()),
                ],
                input=json.dumps(request),
                text=True,
                capture_output=True,
                timeout=255,
                env=environment,
            )
        if process.returncode:
            raise RuntimeError(
                "MEMORY_MODEL_FAILED: "
                + process.stderr[-1600:]
            )
        return json.loads(process.stdout)

    def embed(
        self,
        texts: list[str],
        *,
        query: bool = False,
    ) -> list[list[float]]:
        return self._run(
            "query" if query else "embed",
            texts,
        )

    def rerank(
        self,
        query: str,
        texts: list[str],
    ) -> list[float]:
        return self._run("rerank", texts, query)


class MemoryAnnIndex:
    """Incremental HNSW view over Runtime's rebuildable memory_nodes table."""

    def __init__(
        self,
        store,
        model_id: str,
    ) -> None:
        self.store = store
        self.model_id = model_id
        self.root = store.root / "ann"
        self.root.mkdir(exist_ok=True)
        with store._connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS memory_ann_state(
                    node_id TEXT PRIMARY KEY,
                    label INTEGER NOT NULL,
                    revision TEXT NOT NULL,
                    status TEXT NOT NULL
                )"""
            )

    def search(
        self,
        vector,
        *,
        filter_sql: str,
        filter_params,
        limit: int = 32,
    ) -> list[tuple[str, float]]:
        import hnswlib
        import numpy as np

        with (self.root / "writer.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with self.store._connect() as db:
                generation = db.execute(
                    "SELECT value FROM memory_service_meta "
                    "WHERE key='ann_generation'"
                ).fetchone()
                metadata = (
                    json.loads(generation[0])
                    if generation
                    else {}
                )
            index = hnswlib.Index(
                space="cosine",
                dim=len(vector),
            )
            filename = self.root / metadata.get(
                "filename",
                "missing",
            )
            rebuilding = (
                metadata.get("model_id") != self.model_id
                or not filename.is_file()
            )
            if rebuilding:
                with self.store._connect() as db:
                    rows = db.execute(
                        """SELECT rowid label,*
                        FROM memory_nodes
                        WHERE status='ACTIVE'
                        AND vector IS NOT NULL
                        AND model_id=?""",
                        (self.model_id,),
                    ).fetchall()
                index.init_index(
                    max_elements=max(1024, len(rows) * 2),
                    ef_construction=100,
                    M=16,
                )
                old = []
            else:
                index.load_index(str(filename))
                with self.store._connect() as db:
                    rows = db.execute(
                        """SELECT n.rowid label,n.*
                        FROM memory_nodes n
                        LEFT JOIN memory_ann_state a
                          ON a.node_id=n.node_id
                        WHERE n.status='ACTIVE'
                        AND n.vector IS NOT NULL
                        AND n.model_id=?
                        AND (
                            a.node_id IS NULL
                            OR a.revision!=n.vector_revision
                            OR a.status!='ACTIVE'
                        )""",
                        (self.model_id,),
                    ).fetchall()
                    old = db.execute(
                        """SELECT a.*
                        FROM memory_ann_state a
                        LEFT JOIN memory_nodes n
                          ON n.node_id=a.node_id
                        WHERE a.status='ACTIVE'
                        AND (
                            n.node_id IS NULL
                            OR n.status!='ACTIVE'
                            OR n.model_id!=?
                        )""",
                        (self.model_id,),
                    ).fetchall()

            if (
                index.get_current_count() + len(rows)
                > index.get_max_elements()
            ):
                index.resize_index(
                    (
                        index.get_current_count()
                        + len(rows)
                    )
                    * 2
                    + 1024
                )

            changes: list[tuple[str, int, str, str]] = []
            for row in old:
                index.mark_deleted(row["label"])
                changes.append(
                    (
                        row["node_id"],
                        row["label"],
                        row["revision"],
                        "INACTIVE",
                    )
                )

            for row in rows:
                if not rebuilding:
                    with self.store._connect() as db:
                        previous = db.execute(
                            "SELECT status FROM memory_ann_state "
                            "WHERE node_id=?",
                            (row["node_id"],),
                        ).fetchone()
                    if previous and previous[0] != "ACTIVE":
                        index.unmark_deleted(row["label"])
                index.add_items(
                    np.asarray(
                        [json.loads(row["vector"])],
                        dtype=np.float32,
                    ),
                    np.asarray(
                        [row["label"]],
                        dtype=np.int64,
                    ),
                    num_threads=1,
                )
                changes.append(
                    (
                        row["node_id"],
                        row["label"],
                        row["vector_revision"],
                        "ACTIVE",
                    )
                )

            if rebuilding or changes:
                new_name = (
                    hashlib.sha256(
                        self.model_id.encode()
                    ).hexdigest()[:12]
                    + "-"
                    + uuid4().hex
                    + ".bin"
                )
                index.save_index(
                    str(self.root / new_name)
                )
                with self.store._connect() as db:
                    if rebuilding:
                        db.execute(
                            "DELETE FROM memory_ann_state"
                        )
                    db.executemany(
                        "INSERT OR REPLACE INTO "
                        "memory_ann_state VALUES(?,?,?,?)",
                        changes,
                    )
                    db.execute(
                        "INSERT OR REPLACE INTO "
                        "memory_service_meta VALUES("
                        "'ann_generation',?)",
                        (
                            json.dumps(
                                {
                                    "filename": new_name,
                                    "model_id": self.model_id,
                                }
                            ),
                        ),
                    )
                for old_file in self.root.glob("*.bin"):
                    if old_file.name != new_name:
                        old_file.unlink(missing_ok=True)

            with self.store._connect() as db:
                allowed = db.execute(
                    "SELECT n.rowid label,n.node_id "
                    "FROM memory_nodes n WHERE "
                    + filter_sql
                    + " AND n.vector IS NOT NULL "
                    "AND n.model_id=?",
                    [*filter_params, self.model_id],
                ).fetchall()
            labels = {
                row["label"]: row["node_id"]
                for row in allowed
            }
            if not labels:
                return []
            count = min(limit, len(labels))
            index.set_ef(max(64, count * 4))
            nearest, distances = index.knn_query(
                np.asarray(
                    [vector],
                    dtype=np.float32,
                ),
                k=count,
                num_threads=1,
                filter=lambda label: label in labels,
            )
            return [
                (
                    labels[int(label)],
                    1 - float(distance),
                )
                for label, distance in zip(
                    nearest[0],
                    distances[0],
                    strict=True,
                )
            ]


def worker() -> None:
    """Entry point for one transient embedding/reranking process."""
    import numpy as np

    started = time.monotonic()
    request = json.load(sys.stdin)
    if request["operation"] == "rerank":
        from fastembed.rerank.cross_encoder import (
            TextCrossEncoder,
        )

        if request["reranker"] == _DEFAULT_RERANKER:
            from fastembed.common.model_description import (
                ModelSource,
            )

            TextCrossEncoder.add_custom_model(
                _DEFAULT_RERANKER,
                sources=ModelSource(
                    hf="Xenova/bge-reranker-base"
                ),
                model_file="onnx/model_quantized.onnx",
                license="mit",
                size_in_gb=0.28,
                description=(
                    "Quantized BGE bilingual "
                    "Chinese/English cross-encoder"
                ),
            )
        model = TextCrossEncoder(
            request["reranker"],
            cache_dir=request["cache"],
            threads=2,
            local_files_only=False,
            enable_cpu_mem_arena=False,
        )
        result = [
            float(value)
            for value in model.rerank(
                request["query"],
                request["texts"],
                batch_size=1,
            )
        ]
    else:
        from fastembed import TextEmbedding

        model = TextEmbedding(
            request["embedding"],
            cache_dir=request["cache"],
            threads=2,
            local_files_only=False,
        )
        vectors = (
            model.query_embed(
                request["texts"],
                batch_size=1,
            )
            if request["operation"] == "query"
            else model.embed(
                request["texts"],
                batch_size=1,
            )
        )
        result = [
            (
                vector
                / max(
                    float(np.linalg.norm(vector)),
                    1e-12,
                )
            ).tolist()
            for vector in vectors
        ]

    metrics = {
        "operation": request["operation"],
        "documents": len(request["texts"]),
        "elapsed_seconds": round(
            time.monotonic() - started,
            3,
        ),
        "max_rss_kib": resource.getrusage(
            resource.RUSAGE_SELF
        ).ru_maxrss,
        "completed_at": time.time(),
        "pid": os.getpid(),
    }
    with (
        Path(request["cache"])
        / "inference-metrics.jsonl"
    ).open("a") as output:
        output.write(json.dumps(metrics) + "\n")
    json.dump(result, sys.stdout)


if __name__ == "__main__":
    worker()


__all__ = [
    "LocalRetrievalModels",
    "MemoryAnnIndex",
    "worker",
]
