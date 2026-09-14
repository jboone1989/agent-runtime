"""Bounded local OCR and vision extraction backed by PaddleOCR."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math
from pathlib import Path
import tempfile
from typing import Any

from ..capabilities.inventory import CapabilityInventory
from ..capabilities.model import (
    CapabilityAccessMode,
    CapabilityBinding,
    CapabilityKind,
    CapabilityLifecycleState,
    SideEffectLevel,
)
from .documents import _BoundDocumentRoot, _relative_document_path

IMAGE_OCR = "image.ocr"
VISION_EXTRACT = "vision.extract"
_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"})


@dataclass(frozen=True)
class VisionPolicy:
    max_input_bytes: int = 32 * 1024 * 1024
    max_text_chars: int = 250_000
    max_lines: int = 5_000
    max_results: int = 8
    allowed_extensions: frozenset[str] = _IMAGE_EXTENSIONS

    def __post_init__(self) -> None:
        if not 1 <= int(self.max_input_bytes) <= 256 * 1024 * 1024:
            raise ValueError("max_input_bytes is outside the supported range")
        if not 1 <= int(self.max_text_chars) <= 2_000_000:
            raise ValueError("max_text_chars is outside the supported range")
        if not 1 <= int(self.max_lines) <= 50_000:
            raise ValueError("max_lines is outside the supported range")
        if not 1 <= int(self.max_results) <= 100:
            raise ValueError("max_results is outside the supported range")
        extensions = frozenset(
            item.lower() if item.startswith(".") else f".{item.lower()}"
            for item in self.allowed_extensions
            if isinstance(item, str) and item.strip()
        )
        if not extensions:
            raise ValueError("allowed_extensions must be non-empty")
        object.__setattr__(self, "allowed_extensions", extensions)


class PaddleOCRProvider:
    provider_name = "paddleocr"
    contract_version = "runtime-vision:v1"

    def __init__(
        self,
        *,
        roots: Mapping[str, str | Path],
        engine: Any,
        policy: VisionPolicy | None = None,
    ) -> None:
        if not roots or any(not isinstance(key, str) or not key for key in roots):
            raise ValueError("vision roots require non-empty identifiers")
        if engine is None or not callable(getattr(engine, "predict", None)):
            raise TypeError("vision provider requires a configured PaddleOCR-compatible engine")
        self.policy = policy or VisionPolicy()
        self._roots = {key: _BoundDocumentRoot(value) for key, value in roots.items()}
        self._engine = engine

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
                {**common, "capability_id": IMAGE_OCR},
                {**common, "capability_id": VISION_EXTRACT},
            ],
        }

    def invoke(
        self, capability_id: str, payload: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        if capability_id not in {IMAGE_OCR, VISION_EXTRACT}:
            raise ValueError(f"unsupported vision capability: {capability_id}")
        request = dict(payload or {})
        unknown = sorted(set(request) - {"root_id", "path"})
        if unknown:
            raise ValueError(f"unsupported vision parameters: {', '.join(unknown)}")
        root_id = request.get("root_id")
        if root_id is None and len(self._roots) == 1:
            root_id = next(iter(self._roots))
        if not isinstance(root_id, str) or root_id not in self._roots:
            raise ValueError("a configured vision root_id is required")
        logical = _relative_document_path(request.get("path"))
        extension = logical.suffix.lower()
        if extension not in self.policy.allowed_extensions:
            raise ValueError("image extension is not allowed by policy")
        raw, relative_path = self._roots[root_id].read(logical.as_posix(), self.policy.max_input_bytes)

        with tempfile.TemporaryDirectory(prefix="agent-runtime-ocr-") as directory:
            temp_path = Path(directory) / f"input{extension}"
            temp_path.write_bytes(raw)
            predicted = self._engine.predict(str(temp_path))
            lines, truncated = self._normalize(predicted, include_boxes=capability_id == VISION_EXTRACT)

        text = "\n".join(line["text"] for line in lines)
        return {
            "ok": True,
            "provider": self.provider_name,
            "capability_id": capability_id,
            "root_id": root_id,
            "path": relative_path,
            "input_bytes": len(raw),
            "text": text,
            "lines": lines,
            "line_count": len(lines),
            "truncated": truncated,
            "model_acquisition_during_invoke": False,
        }

    def close(self) -> None:
        for root in self._roots.values():
            root.close()

    def _normalize(self, predicted: Any, *, include_boxes: bool) -> tuple[list[dict[str, Any]], bool]:
        rows: list[dict[str, Any]] = []
        used_chars = 0
        truncated = False
        for result_index, result in enumerate(predicted):
            if result_index >= self.policy.max_results:
                truncated = True
                break
            data = _result_mapping(result)
            texts = data.get("rec_texts", [])
            scores = data.get("rec_scores", [])
            boxes = data.get("rec_boxes", [])
            if not isinstance(texts, (list, tuple)):
                continue
            for index, value in enumerate(texts):
                if len(rows) >= self.policy.max_lines:
                    truncated = True
                    break
                text = str(value).strip()
                if not text:
                    continue
                remaining = self.policy.max_text_chars - used_chars
                separator = 1 if rows else 0
                if remaining <= separator:
                    truncated = True
                    break
                remaining -= separator
                bounded = text[:remaining]
                if len(bounded) < len(text):
                    truncated = True
                row: dict[str, Any] = {
                    "text": bounded,
                    "confidence": _score(scores[index] if index < len(scores) else None),
                }
                if include_boxes and index < len(boxes):
                    box = _box(boxes[index])
                    if box is not None:
                        row["bbox"] = box
                rows.append(row)
                used_chars += separator + len(bounded)
                if truncated:
                    break
            if truncated:
                break
        return rows, truncated


def register_vision_provider(
    provider: PaddleOCRProvider, inventory: CapabilityInventory
) -> tuple[str, ...]:
    registered: list[str] = []
    for capability_id in (IMAGE_OCR, VISION_EXTRACT):
        if inventory.get(capability_id) is not None:
            raise ValueError(f"vision capability already registered: {capability_id}")
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
                entry_point={"adapter": "PaddleOCRProvider", "capability_id": capability_id},
                evidence_refs=["contract:runtime-vision:v1"],
            )
        )
        registered.append(capability_id)
    return tuple(registered)


def create_paddleocr_engine(**kwargs: Any) -> Any:
    """Explicit deployment-time engine construction; model acquisition is not hidden in invoke()."""
    try:
        from paddleocr import PaddleOCR
    except ImportError as exc:
        raise RuntimeError("vision extra missing; install agent-runtime[vision]") from exc
    defaults = {
        "use_doc_orientation_classify": False,
        "use_doc_unwarping": False,
        "use_textline_orientation": False,
        "engine": "paddle",
    }
    defaults.update(kwargs)
    return PaddleOCR(**defaults)


def _result_mapping(result: Any) -> Mapping[str, Any]:
    if isinstance(result, Mapping):
        raw = result
    else:
        raw = getattr(result, "json", None)
        if callable(raw):
            raw = raw()
        if not isinstance(raw, Mapping):
            raw = getattr(result, "res", None)
    if not isinstance(raw, Mapping):
        return {}
    nested = raw.get("res")
    return nested if isinstance(nested, Mapping) else raw


def _score(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _box(value: Any) -> list[float] | None:
    try:
        items = list(value.tolist() if hasattr(value, "tolist") else value)
    except (TypeError, ValueError):
        return None
    if len(items) != 4:
        return None
    output: list[float] = []
    for item in items:
        try:
            number = float(item)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(number):
            return None
        output.append(number)
    return output


__all__ = [
    "IMAGE_OCR",
    "VISION_EXTRACT",
    "PaddleOCRProvider",
    "VisionPolicy",
    "create_paddleocr_engine",
    "register_vision_provider",
]
