from __future__ import annotations

import importlib
import json
import sys
from types import SimpleNamespace

import pytest

from agent_runtime.memory.durable import CanonicalMemoryStore
from agent_runtime.memory.retrieval import DurableMemoryRetrieval


def test_optional_provider_import_does_not_load_heavy_backends():
    before_fastembed = "fastembed" in sys.modules
    before_hnswlib = "hnswlib" in sys.modules

    module = importlib.import_module(
        "agent_runtime.memory.local_retrieval"
    )

    assert module.LocalRetrievalModels
    assert module.MemoryAnnIndex
    assert ("fastembed" in sys.modules) == before_fastembed
    assert ("hnswlib" in sys.modules) == before_hnswlib


def test_generic_env_names_override_legacy_names(monkeypatch, tmp_path):
    module = importlib.import_module(
        "agent_runtime.memory.local_retrieval"
    )
    monkeypatch.setenv(
        "FERRO_MEMORY_EMBEDDING",
        "legacy-embedding",
    )
    monkeypatch.setenv(
        "AGENT_MEMORY_EMBEDDING",
        "generic-embedding",
    )
    monkeypatch.setenv(
        "FERRO_MEMORY_MODEL_CACHE",
        str(tmp_path / "legacy"),
    )
    monkeypatch.setenv(
        "AGENT_MEMORY_MODEL_CACHE",
        str(tmp_path / "generic"),
    )

    models = module.LocalRetrievalModels(tmp_path)

    assert models.embedding == "generic-embedding"
    assert models.cache == str(tmp_path / "generic")


def test_legacy_env_names_remain_compatible(monkeypatch, tmp_path):
    module = importlib.import_module(
        "agent_runtime.memory.local_retrieval"
    )
    monkeypatch.delenv(
        "AGENT_MEMORY_EMBEDDING",
        raising=False,
    )
    monkeypatch.setenv(
        "FERRO_MEMORY_EMBEDDING",
        "legacy-embedding",
    )

    models = module.LocalRetrievalModels(tmp_path)

    assert models.embedding == "legacy-embedding"


def test_hnsw_provider_indexes_runtime_retrieval_rows(tmp_path):
    module = importlib.import_module(
        "agent_runtime.memory.local_retrieval"
    )
    store = CanonicalMemoryStore(
        tmp_path,
        default_subject_id="agent:test",
    )
    retrieval = DurableMemoryRetrieval(store)
    store.append_memory({
        "memory_id": "a",
        "kind": "FACT",
        "text": "alpha",
    })
    store.append_memory({
        "memory_id": "b",
        "kind": "FACT",
        "text": "beta",
    })
    retrieval.sync()

    model_id = "test-model"
    with store._connect() as db:
        for node_id, vector in (
            ("a", [1.0, 0.0, 0.0]),
            ("b", [0.0, 1.0, 0.0]),
        ):
            db.execute(
                """UPDATE memory_nodes
                SET vector=?,model_id=?,vector_revision=?
                WHERE node_id=?""",
                (
                    json.dumps(vector),
                    model_id,
                    "rev-" + node_id,
                    node_id,
                ),
            )

    index = module.MemoryAnnIndex(store, model_id)
    result = index.search(
        [1.0, 0.0, 0.0],
        filter_sql="n.status='ACTIVE'",
        filter_params=(),
        limit=2,
    )

    assert result[0][0] == "a"
    assert {node_id for node_id, _ in result} == {"a", "b"}


def test_local_model_retries_short_first_load_failure_and_marks_cache_ready(
    monkeypatch,
    tmp_path,
):
    module = importlib.import_module(
        "agent_runtime.memory.local_retrieval"
    )
    cache = tmp_path / "cache"
    monkeypatch.setenv("AGENT_MEMORY_MODEL_CACHE", str(cache))
    calls = []

    def fake_run(*_args, **kwargs):
        calls.append(json.loads(kwargs["input"]))
        if len(calls) == 1:
            return SimpleNamespace(
                returncode=1,
                stdout="",
                stderr=(
                    "Warning: You are sending unauthenticated requests "
                    "to the HF Hub.\n"
                    "Fetching 5 files: 100%|##########| 5/5\n"
                    "temporary loader exit"
                ),
            )
        return SimpleNamespace(
            returncode=0,
            stdout="[[0.1, 0.2]]",
            stderr="",
        )

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    models = module.LocalRetrievalModels(tmp_path)

    assert models.embed(["alpha"], query=True) == [[0.1, 0.2]]
    assert len(calls) == 2
    assert calls[0]["local_files_only"] is False
    assert len(list((cache / "ready").glob("*.json"))) == 1

    calls.clear()
    assert models.embed(["alpha"], query=True) == [[0.1, 0.2]]
    assert calls[0]["local_files_only"] is True


def test_local_model_failure_reports_worker_result_without_hf_progress_noise(
    monkeypatch,
    tmp_path,
):
    module = importlib.import_module(
        "agent_runtime.memory.local_retrieval"
    )
    monkeypatch.setenv(
        "AGENT_MEMORY_MODEL_CACHE",
        str(tmp_path / "cache"),
    )

    def fail(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=137,
            stdout="",
            stderr=(
                "Fetching 5 files: 80%|########  | 4/5\n"
                "worker killed"
            ),
        )

    monkeypatch.setattr(module.subprocess, "run", fail)
    models = module.LocalRetrievalModels(tmp_path)

    with pytest.raises(RuntimeError) as error:
        models.embed(["alpha"], query=True)

    message = str(error.value)
    assert "operation=query" in message
    assert "returncode=137" in message
    assert "worker killed" in message
    assert "Fetching 5 files" not in message
