"""Bounded local audio transcription backed by faster-whisper."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import io
from pathlib import Path
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

AUDIO_TRANSCRIBE = "audio.transcribe"
_AUDIO_EXTENSIONS = frozenset({".wav", ".mp3", ".m4a", ".flac", ".ogg", ".opus", ".webm", ".mp4"})


@dataclass(frozen=True)
class AudioTranscriptionPolicy:
    max_input_bytes: int = 64 * 1024 * 1024
    max_output_chars: int = 250_000
    max_segments: int = 5_000
    max_beam_size: int = 10
    allowed_extensions: frozenset[str] = _AUDIO_EXTENSIONS

    def __post_init__(self) -> None:
        if not 1 <= int(self.max_input_bytes) <= 512 * 1024 * 1024:
            raise ValueError("max_input_bytes is outside the supported range")
        if not 1 <= int(self.max_output_chars) <= 2_000_000:
            raise ValueError("max_output_chars is outside the supported range")
        if not 1 <= int(self.max_segments) <= 50_000:
            raise ValueError("max_segments is outside the supported range")
        if not 1 <= int(self.max_beam_size) <= 20:
            raise ValueError("max_beam_size is outside the supported range")
        extensions = frozenset(
            item.lower() if item.startswith(".") else f".{item.lower()}"
            for item in self.allowed_extensions
            if isinstance(item, str) and item.strip()
        )
        if not extensions:
            raise ValueError("allowed_extensions must be non-empty")
        object.__setattr__(self, "allowed_extensions", extensions)


class FasterWhisperProvider:
    provider_name = "faster-whisper"
    contract_version = "runtime-audio-transcribe:v1"

    def __init__(
        self,
        *,
        roots: Mapping[str, str | Path],
        policy: AudioTranscriptionPolicy | None = None,
        model: Any | None = None,
        model_path: str | Path | None = None,
        device: str = "cpu",
        compute_type: str = "int8",
    ) -> None:
        if not roots or any(not isinstance(key, str) or not key for key in roots):
            raise ValueError("audio roots require non-empty identifiers")
        if model is None and model_path is None:
            raise ValueError("audio provider requires an injected model or local model_path")
        self.policy = policy or AudioTranscriptionPolicy()
        self._roots = {key: _BoundDocumentRoot(value) for key, value in roots.items()}
        self._model = model
        self._model_path = None if model_path is None else str(model_path)
        self._device = str(device)
        self._compute_type = str(compute_type)

    def manifest(self) -> dict[str, Any]:
        return {
            "provider": self.provider_name,
            "version": self.contract_version,
            "capabilities": [
                {
                    "capability_id": AUDIO_TRANSCRIBE,
                    "provider": self.provider_name,
                    "version": self.contract_version,
                    "side_effect_level": "READ_ONLY",
                    "required_permissions": [],
                }
            ],
        }

    def invoke(
        self, capability_id: str, payload: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        if capability_id != AUDIO_TRANSCRIBE:
            raise ValueError(f"unsupported audio capability: {capability_id}")
        request = dict(payload or {})
        unknown = sorted(set(request) - {"root_id", "path", "language", "beam_size", "vad_filter"})
        if unknown:
            raise ValueError(f"unsupported audio parameters: {', '.join(unknown)}")
        root_id = request.get("root_id")
        if root_id is None and len(self._roots) == 1:
            root_id = next(iter(self._roots))
        if not isinstance(root_id, str) or root_id not in self._roots:
            raise ValueError("a configured audio root_id is required")
        logical = _relative_document_path(request.get("path"))
        extension = logical.suffix.lower()
        if extension not in self.policy.allowed_extensions:
            raise ValueError("audio extension is not allowed by policy")
        raw, relative_path = self._roots[root_id].read(logical.as_posix(), self.policy.max_input_bytes)
        language = request.get("language")
        if language is not None and (
            not isinstance(language, str) or not 1 <= len(language.strip()) <= 16
        ):
            raise ValueError("language must be a bounded string or null")
        beam_size = request.get("beam_size", 5)
        if isinstance(beam_size, bool) or not isinstance(beam_size, int) or not 1 <= beam_size <= self.policy.max_beam_size:
            raise ValueError("beam_size exceeds configured bound")
        vad_filter = request.get("vad_filter", True)
        if not isinstance(vad_filter, bool):
            raise ValueError("vad_filter must be boolean")

        segments, info = self._ensure_model().transcribe(
            io.BytesIO(raw),
            language=None if language is None else language.strip(),
            task="transcribe",
            beam_size=beam_size,
            vad_filter=vad_filter,
        )
        rows: list[dict[str, Any]] = []
        transcript_parts: list[str] = []
        used_chars = 0
        truncated = False
        for index, segment in enumerate(segments):
            if index >= self.policy.max_segments:
                truncated = True
                break
            text = str(getattr(segment, "text", ""))
            remaining = self.policy.max_output_chars - used_chars
            if remaining <= 0:
                truncated = True
                break
            bounded = text[:remaining]
            if len(bounded) < len(text):
                truncated = True
            rows.append(
                {
                    "start": _finite_float(getattr(segment, "start", 0.0)),
                    "end": _finite_float(getattr(segment, "end", 0.0)),
                    "text": bounded,
                }
            )
            transcript_parts.append(bounded)
            used_chars += len(bounded)
            if truncated:
                break
        return {
            "ok": True,
            "provider": self.provider_name,
            "capability_id": AUDIO_TRANSCRIBE,
            "root_id": root_id,
            "path": relative_path,
            "input_bytes": len(raw),
            "language": _bounded_optional(getattr(info, "language", None), 32),
            "language_probability": _optional_float(getattr(info, "language_probability", None)),
            "duration_seconds": _optional_float(getattr(info, "duration", None)),
            "text": "".join(transcript_parts),
            "segments": rows,
            "segment_count": len(rows),
            "truncated": truncated,
            "model_downloads_allowed": False,
        }

    def close(self) -> None:
        for root in self._roots.values():
            root.close()

    def _ensure_model(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError("audio extra missing; install agent-runtime[audio]") from exc
        self._model = WhisperModel(
            self._model_path,
            device=self._device,
            compute_type=self._compute_type,
            local_files_only=True,
        )
        return self._model


def register_audio_provider(
    provider: FasterWhisperProvider, inventory: CapabilityInventory
) -> tuple[str, ...]:
    if inventory.get(AUDIO_TRANSCRIBE) is not None:
        raise ValueError(f"audio capability already registered: {AUDIO_TRANSCRIBE}")
    inventory.register(
        CapabilityBinding(
            capability_id=AUDIO_TRANSCRIBE,
            kind=CapabilityKind.LIBRARY,
            provider=provider.provider_name,
            candidate_id=f"{provider.provider_name}:{AUDIO_TRANSCRIBE}",
            access_mode=CapabilityAccessMode.LOCAL,
            state=CapabilityLifecycleState.ACTIVE,
            version=provider.contract_version,
            side_effects=SideEffectLevel.READ_ONLY,
            entry_point={"adapter": "FasterWhisperProvider", "capability_id": AUDIO_TRANSCRIBE},
            evidence_refs=["contract:runtime-audio-transcribe:v1"],
        )
    )
    return (AUDIO_TRANSCRIBE,)


def _finite_float(value: Any) -> float:
    number = float(value)
    if number != number or number in {float("inf"), float("-inf")}:
        return 0.0
    return number


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return _finite_float(value)
    except (TypeError, ValueError):
        return None


def _bounded_optional(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    return str(value)[:limit]


__all__ = [
    "AUDIO_TRANSCRIBE",
    "AudioTranscriptionPolicy",
    "FasterWhisperProvider",
    "register_audio_provider",
]
