"""Bounded local-document parsing through a provider-neutral Runtime contract."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import io
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any

from ..capabilities.inventory import CapabilityInventory
from ..capabilities.model import (
    CapabilityAccessMode,
    CapabilityBinding,
    CapabilityKind,
    CapabilityLifecycleState,
    SideEffectLevel,
)


DOCUMENT_PARSE = "document.parse"
DOCUMENT_EXTRACT = "document.extract"
_DEFAULT_EXTENSIONS = frozenset(
    {
        ".pdf",
        ".docx",
        ".pptx",
        ".xlsx",
        ".xls",
        ".html",
        ".htm",
        ".txt",
        ".md",
        ".csv",
        ".json",
        ".xml",
        ".epub",
        ".msg",
        ".eml",
    }
)


@dataclass(frozen=True)
class DocumentPolicy:
    max_input_bytes: int = 16 * 1024 * 1024
    max_output_chars: int = 500_000
    allowed_extensions: frozenset[str] = _DEFAULT_EXTENSIONS

    def __post_init__(self) -> None:
        if self.max_input_bytes < 1 or self.max_output_chars < 1:
            raise ValueError("document size bounds must be positive")
        normalized = frozenset(
            item.lower() if item.startswith(".") else f".{item.lower()}"
            for item in self.allowed_extensions
            if isinstance(item, str) and item.strip()
        )
        if not normalized:
            raise ValueError("document allowed_extensions must be non-empty")
        object.__setattr__(self, "allowed_extensions", normalized)


class _BoundDocumentRoot:
    """Open files below one pinned root without following redirected components."""

    def __init__(self, root: str | Path) -> None:
        path = Path(os.path.abspath(root))
        if not path.is_dir() or path.is_symlink():
            raise ValueError("document root must be an existing real directory")
        self.path = path
        metadata = path.stat(follow_symlinks=False)
        self.identity = (metadata.st_dev, metadata.st_ino, metadata.st_mode)
        self._fd: int | None = None
        flags = getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        if flags:
            try:
                self._fd = os.open(path, os.O_RDONLY | flags)
            except OSError as exc:
                raise ValueError("document root cannot be pinned safely") from exc

    def close(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def _check(self) -> None:
        if self._fd is None:
            raise RuntimeError("document root is closed or unsupported")
        metadata = os.fstat(self._fd)
        if (metadata.st_dev, metadata.st_ino, metadata.st_mode) != self.identity:
            raise RuntimeError("document root identity changed")

    def read(self, raw_path: object, max_bytes: int) -> tuple[bytes, str]:
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError("document path must be a non-empty relative string")
        logical = PurePosixPath(raw_path.replace("\\", "/"))
        if logical.is_absolute() or any(part in {"", ".", ".."} for part in logical.parts):
            raise ValueError("document path must stay within the bound root")
        self._check()
        current_fd = os.dup(self._fd)
        try:
            for index, part in enumerate(logical.parts):
                flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                if index < len(logical.parts) - 1:
                    flags |= getattr(os, "O_DIRECTORY", 0)
                next_fd = os.open(part, flags, dir_fd=current_fd)
                os.close(current_fd)
                current_fd = next_fd
            metadata = os.fstat(current_fd)
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError("document path must identify a regular file")
            with os.fdopen(current_fd, "rb", closefd=False) as stream:
                raw = stream.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raise ValueError("document exceeds configured input byte limit")
            self._check()
            return raw, logical.as_posix()
        finally:
            os.close(current_fd)


class MarkItDownDocumentProvider:
    """Read bound local files and convert them with Microsoft MarkItDown."""

    provider_name = "markitdown"
    contract_version = "runtime-document:v1"

    def __init__(
        self,
        *,
        roots: Mapping[str, str | Path],
        policy: DocumentPolicy | None = None,
        converter: Any | None = None,
    ) -> None:
        if not roots:
            raise ValueError("at least one document root is required")
        if any(not isinstance(key, str) or not key for key in roots):
            raise ValueError("document root identifiers must be non-empty strings")
        self.policy = policy or DocumentPolicy()
        self._roots = {key: _BoundDocumentRoot(value) for key, value in roots.items()}
        self._converter = converter

    def manifest(self) -> dict[str, Any]:
        common = {
            "provider": self.provider_name,
            "version": self.contract_version,
            "side_effect_level": "READ_ONLY",
            "required_permissions": [],
        }
        return {
            "provider": self.provider_name,
            "version": self.contract_version,
            "capabilities": [
                {
                    **common,
                    "capability_id": DOCUMENT_PARSE,
                    "description": "Parse one approved local document into bounded Markdown.",
                },
                {
                    **common,
                    "capability_id": DOCUMENT_EXTRACT,
                    "description": "Extract a bounded plain-text projection from one approved local document.",
                },
            ],
        }

    def invoke(
        self,
        capability_id: str,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if capability_id not in {DOCUMENT_PARSE, DOCUMENT_EXTRACT}:
            raise ValueError(f"unsupported document capability: {capability_id}")
        request = dict(payload or {})
        root_id = request.get("root_id")
        if root_id is None and len(self._roots) == 1:
            root_id = next(iter(self._roots))
        if not isinstance(root_id, str) or root_id not in self._roots:
            raise ValueError("a configured document root_id is required")
        requested_max = request.get("max_chars", self.policy.max_output_chars)
        if (
            isinstance(requested_max, bool)
            or not isinstance(requested_max, int)
            or not 1 <= requested_max <= self.policy.max_output_chars
        ):
            raise ValueError("max_chars exceeds configured document output bound")

        raw, relative_path = self._roots[root_id].read(
            request.get("path"), self.policy.max_input_bytes
        )
        extension = PurePosixPath(relative_path).suffix.lower()
        if extension not in self.policy.allowed_extensions:
            raise ValueError("document extension is not allowed by policy")
        converter = self._converter or _load_converter()
        result = converter.convert_stream(
            io.BytesIO(raw), file_extension=extension
        )
        markdown = getattr(result, "markdown", None)
        if markdown is None:
            markdown = getattr(result, "text_content", None)
        if not isinstance(markdown, str):
            raise TypeError("document converter returned no Markdown text")
        if markdown.strip() == "This is not a valid Office Open XML file.":
            raise ValueError("document converter rejected invalid Office Open XML")

        rendered = markdown if capability_id == DOCUMENT_PARSE else _markdown_to_text(markdown)
        original_chars = len(rendered)
        bounded = rendered[:requested_max]
        key = "markdown" if capability_id == DOCUMENT_PARSE else "text"
        return {
            "ok": True,
            "provider": self.provider_name,
            "capability_id": capability_id,
            "root_id": root_id,
            "path": relative_path,
            "extension": extension,
            key: bounded,
            "truncated": len(bounded) < original_chars,
            "original_chars": original_chars,
            "input_bytes": len(raw),
        }

    def close(self) -> None:
        for root in self._roots.values():
            root.close()


def register_document_provider(
    provider: MarkItDownDocumentProvider,
    inventory: CapabilityInventory,
) -> tuple[str, ...]:
    registered: list[str] = []
    for row in provider.manifest()["capabilities"]:
        capability_id = str(row["capability_id"])
        if inventory.get(capability_id) is not None:
            raise ValueError(f"document capability already registered: {capability_id}")
        inventory.register(
            CapabilityBinding(
                capability_id=capability_id,
                kind=CapabilityKind.LIBRARY,
                provider=provider.provider_name,
                candidate_id=f"{provider.provider_name}:{capability_id}",
                access_mode=CapabilityAccessMode.LOCAL,
                state=CapabilityLifecycleState.ACTIVE,
                version=provider.contract_version,
                side_effects=SideEffectLevel.READ_ONLY,
                entry_point={
                    "adapter": "MarkItDownDocumentProvider",
                    "capability_id": capability_id,
                },
                evidence_refs=["contract:runtime-document:v1"],
            )
        )
        registered.append(capability_id)
    return tuple(registered)


def _load_converter() -> Any:
    try:
        from markitdown import MarkItDown
    except ImportError as exc:
        raise RuntimeError(
            "document extra missing; install agent-runtime[documents]"
        ) from exc
    return MarkItDown(enable_plugins=False)


def _markdown_to_text(markdown: str) -> str:
    text = re.sub(r"!\[([^]]*)\]\([^)]*\)", r"\1", markdown)
    text = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-+*]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+[.)]\s+", "", text, flags=re.MULTILINE)
    text = text.replace("```", "")
    text = re.sub(r"[*_~`]", "", text)
    return text.strip()


__all__ = [
    "DOCUMENT_EXTRACT",
    "DOCUMENT_PARSE",
    "DocumentPolicy",
    "MarkItDownDocumentProvider",
    "register_document_provider",
]
