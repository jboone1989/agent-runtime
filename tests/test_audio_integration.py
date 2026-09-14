from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_runtime.capabilities.inventory import CapabilityInventory
from agent_runtime.capabilities.model import SideEffectLevel
from agent_runtime.integrations.audio import (
    AUDIO_TRANSCRIBE,
    AudioTranscriptionPolicy,
    FasterWhisperProvider,
    register_audio_provider,
)


class _Model:
    def __init__(self):
        self.calls = []

    def transcribe(self, audio, **kwargs):
        self.calls.append((audio.read(), kwargs))
        segments = iter(
            [
                SimpleNamespace(start=0.0, end=1.0, text="hello "),
                SimpleNamespace(start=1.0, end=2.0, text="world"),
            ]
        )
        info = SimpleNamespace(language="en", language_probability=0.9, duration=2.0)
        return segments, info


def _provider(tmp_path, **policy_kwargs):
    root = tmp_path / "audio"
    root.mkdir()
    (root / "sample.wav").write_bytes(b"wave")
    model = _Model()
    provider = FasterWhisperProvider(
        roots={"media": root},
        policy=AudioTranscriptionPolicy(**policy_kwargs),
        model=model,
    )
    return provider, model, root


def test_audio_transcribe_uses_bound_bytes_and_fixed_transcribe_task(tmp_path) -> None:
    provider, model, _ = _provider(tmp_path)
    result = provider.invoke(
        AUDIO_TRANSCRIBE,
        {"path": "sample.wav", "language": "en", "beam_size": 3, "vad_filter": False},
    )

    assert result["ok"] is True
    assert result["text"] == "hello world"
    assert result["segment_count"] == 2
    assert result["language"] == "en"
    assert result["model_downloads_allowed"] is False
    raw, kwargs = model.calls[0]
    assert raw == b"wave"
    assert kwargs == {"language": "en", "task": "transcribe", "beam_size": 3, "vad_filter": False}
    provider.close()


def test_audio_output_and_segment_count_are_bounded(tmp_path) -> None:
    provider, _, _ = _provider(tmp_path, max_output_chars=7, max_segments=5)
    result = provider.invoke(AUDIO_TRANSCRIBE, {"path": "sample.wav"})
    assert result["text"] == "hello w"
    assert result["truncated"] is True
    assert result["segments"][-1]["text"] == "w"
    provider.close()


def test_audio_rejects_paths_extensions_and_unapproved_controls(tmp_path) -> None:
    provider, _, root = _provider(tmp_path)
    (root / "note.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="within"):
        provider.invoke(AUDIO_TRANSCRIBE, {"path": "../sample.wav"})
    with pytest.raises(ValueError, match="extension"):
        provider.invoke(AUDIO_TRANSCRIBE, {"path": "note.txt"})
    with pytest.raises(ValueError, match="unsupported audio parameters"):
        provider.invoke(AUDIO_TRANSCRIBE, {"path": "sample.wav", "prompt": "x"})
    provider.close()


def test_audio_symlink_is_not_followed(tmp_path) -> None:
    provider, _, root = _provider(tmp_path)
    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"outside")
    link = root / "link.wav"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(OSError):
        provider.invoke(AUDIO_TRANSCRIBE, {"path": "link.wav"})
    provider.close()


def test_audio_inventory_is_read_only(tmp_path) -> None:
    provider, _, _ = _provider(tmp_path)
    inventory = CapabilityInventory()
    assert register_audio_provider(provider, inventory) == (AUDIO_TRANSCRIBE,)
    binding = inventory.get(AUDIO_TRANSCRIBE)
    assert binding.side_effects is SideEffectLevel.READ_ONLY
    assert binding.access_mode.value == "LOCAL"
    provider.close()
