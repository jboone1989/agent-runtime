from dataclasses import asdict

import pytest

from agent_runtime.memory.durable import CanonicalMemoryStore
from agent_runtime.memory.documents import DocumentHierarchy
from agent_runtime.memory.projection_scope import ProjectionScope

SCOPE = ProjectionScope("t", "o", "a", "PRIVATE_OWNER")


def test_real_sdk_spans_recursive_links_and_replay_without_parser(tmp_path, monkeypatch):
    store = CanonicalMemoryStore(tmp_path)
    view = DocumentHierarchy(store, SCOPE, chunk_sizes=(128, 64, 32), overlap=4)
    text = "\n\n".join("Section " + str(i) + ". Bitcoin and AI evidence is read from its original source. " * 8 for i in range(12))
    artifact = view.parse("e1", text, source_version="v1")
    assert artifact["scope"] == asdict(SCOPE)
    assert {c["depth"] for c in artifact["chunks"]} == {0, 1, 2}
    ids = {c["node_id"] for c in artifact["chunks"]}
    assert len(ids) == len(artifact["chunks"])
    for chunk in artifact["chunks"]:
        span = chunk["source_spans"][0]
        assert text[span["start"]:span["end"]] == chunk["body"]
        if chunk["depth"]:
            assert chunk["parent_id"] in ids
    roots = view.expand(artifact, None)
    assert roots and view.expand(artifact, roots[0]["node_id"])
    from llama_index.core.node_parser import HierarchicalNodeParser
    monkeypatch.setattr(HierarchicalNodeParser, "from_defaults", lambda **kw: pytest.fail("replay called the parser"))
    assert DocumentHierarchy(store, SCOPE, chunk_sizes=(128, 64, 32), overlap=4).parse("e1", text, source_version="v1") == artifact
    with pytest.raises(ValueError, match="source version changed"):
        view.parse("e1", text + " changed", source_version="v1")


def test_scope_and_explicit_source_budget(tmp_path):
    store = CanonicalMemoryStore(tmp_path)
    view = DocumentHierarchy(store, SCOPE, chunk_sizes=(64, 32), overlap=4, max_source_chars=10)
    with pytest.raises(ValueError, match="BUDGET_EXHAUSTED"):
        view.parse("e", "X" * 11, source_version="1")
    first = view.parse("e", "Hello", source_version="1")
    other = DocumentHierarchy(store, ProjectionScope("t", "o", "b", "PRIVATE_OWNER"), chunk_sizes=(64, 32), overlap=4, max_source_chars=10)
    second = other.parse("e", "Hello", source_version="1")
    assert first["chunks"][0]["node_id"] != second["chunks"][0]["node_id"]
