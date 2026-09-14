from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_runtime.application import ApplicationRuntime
from agent_runtime.capabilities.inventory import CapabilityInventory
from agent_runtime.capabilities.model import CapabilityLifecycleState, SideEffectLevel
from agent_runtime.integrations.documents import (
    DOCUMENT_EXTRACT,
    DOCUMENT_PARSE,
    DocumentPolicy,
    MarkItDownDocumentProvider,
    register_document_provider,
)


class _Converter:
    def __init__(self, markdown: str = "# Title\n\nHello **world**") -> None:
        self.markdown = markdown
        self.calls = []

    def convert_stream(self, stream, *, file_extension=None, **kwargs):
        raw = stream.read()
        self.calls.append((raw, file_extension))
        return SimpleNamespace(markdown=self.markdown)


def test_document_provider_parses_only_bound_local_file(tmp_path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "note.txt").write_text("source", encoding="utf-8")
    converter = _Converter()
    provider = MarkItDownDocumentProvider(roots={"docs": docs}, converter=converter)

    result = provider.invoke(
        DOCUMENT_PARSE, {"root_id": "docs", "path": "note.txt"}
    )

    assert result["ok"] is True
    assert result["markdown"].startswith("# Title")
    assert result["input_bytes"] == 6
    assert converter.calls == [(b"source", ".txt")]


def test_document_extract_returns_bounded_plain_text(tmp_path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "note.md").write_text("ignored", encoding="utf-8")
    provider = MarkItDownDocumentProvider(
        roots={"docs": docs},
        converter=_Converter("# Heading\n\nSee [site](https://example.com) and **bold**."),
        policy=DocumentPolicy(max_output_chars=20),
    )

    result = provider.invoke(
        DOCUMENT_EXTRACT,
        {"root_id": "docs", "path": "note.md", "max_chars": 12},
    )

    assert result["text"] == "Heading\n\nSee"
    assert result["truncated"] is True
    assert result["original_chars"] > 12


def test_document_provider_rejects_escape_symlink_extension_and_size(tmp_path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    (docs / "large.txt").write_bytes(b"x" * 9)
    (docs / "code.py").write_text("print('x')", encoding="utf-8")
    provider = MarkItDownDocumentProvider(
        roots={"docs": docs},
        converter=_Converter(),
        policy=DocumentPolicy(max_input_bytes=8),
    )

    with pytest.raises(ValueError, match="stay within"):
        provider.invoke(DOCUMENT_PARSE, {"root_id": "docs", "path": "../outside.txt"})
    with pytest.raises(ValueError, match="input byte limit"):
        provider.invoke(DOCUMENT_PARSE, {"root_id": "docs", "path": "large.txt"})
    with pytest.raises(ValueError, match="extension"):
        provider.invoke(DOCUMENT_PARSE, {"root_id": "docs", "path": "code.py"})

    link = docs / "link.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    with pytest.raises(OSError):
        provider.invoke(DOCUMENT_PARSE, {"root_id": "docs", "path": "link.txt"})


def test_document_inventory_and_application_runtime_paths(tmp_path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "note.txt").write_text("source", encoding="utf-8")
    provider = MarkItDownDocumentProvider(roots={"docs": docs}, converter=_Converter())

    inventory = CapabilityInventory()
    registered = register_document_provider(provider, inventory)
    assert registered == (DOCUMENT_PARSE, DOCUMENT_EXTRACT)
    binding = inventory.get(DOCUMENT_PARSE)
    assert binding is not None
    assert binding.state is CapabilityLifecycleState.ACTIVE
    assert binding.side_effects is SideEffectLevel.READ_ONLY

    runtime = ApplicationRuntime.bootstrap(tmp_path / "state", subject_id="subject")
    runtime.register_provider(provider)
    call = runtime.invoke(DOCUMENT_PARSE, {"root_id": "docs", "path": "note.txt"})
    assert call.payload["ok"] is True
    assert runtime.receipts[-1]["provider"] == "markitdown"


def test_invalid_ooxml_marker_fails_closed(tmp_path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "bad.docx").write_bytes(b"bad")
    provider = MarkItDownDocumentProvider(
        roots={"docs": docs},
        converter=_Converter("This is not a valid Office Open XML file."),
    )
    with pytest.raises(ValueError, match="invalid Office"):
        provider.invoke(DOCUMENT_PARSE, {"root_id": "docs", "path": "bad.docx"})
