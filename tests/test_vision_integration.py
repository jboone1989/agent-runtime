from __future__ import annotations

import pytest

from agent_runtime.capabilities.inventory import CapabilityInventory
from agent_runtime.capabilities.model import SideEffectLevel
from agent_runtime.integrations.vision import (
    IMAGE_OCR,
    VISION_EXTRACT,
    PaddleOCRProvider,
    VisionPolicy,
    register_vision_provider,
)


class _Result:
    @property
    def json(self):
        return {
            "res": {
                "rec_texts": ["alpha", "beta"],
                "rec_scores": [0.9, 0.8],
                "rec_boxes": [[1, 2, 30, 40], [5, 6, 50, 60]],
            }
        }


class _Engine:
    def __init__(self):
        self.inputs = []

    def predict(self, path):
        with open(path, "rb") as stream:
            self.inputs.append(stream.read())
        return [_Result()]


def _provider(tmp_path, **policy_kwargs):
    root = tmp_path / "images"
    root.mkdir()
    (root / "sample.png").write_bytes(b"png-bytes")
    engine = _Engine()
    provider = PaddleOCRProvider(
        roots={"images": root},
        engine=engine,
        policy=VisionPolicy(**policy_kwargs),
    )
    return provider, engine, root


def test_image_ocr_returns_text_without_geometry(tmp_path) -> None:
    provider, engine, _ = _provider(tmp_path)
    result = provider.invoke(IMAGE_OCR, {"path": "sample.png"})

    assert result["ok"] is True
    assert result["text"] == "alpha\nbeta"
    assert result["lines"][0] == {"text": "alpha", "confidence": 0.9}
    assert result["model_acquisition_during_invoke"] is False
    assert engine.inputs == [b"png-bytes"]
    provider.close()


def test_vision_extract_includes_bounded_boxes(tmp_path) -> None:
    provider, _, _ = _provider(tmp_path)
    result = provider.invoke(VISION_EXTRACT, {"path": "sample.png"})
    assert result["lines"][0]["bbox"] == [1.0, 2.0, 30.0, 40.0]
    assert result["lines"][1]["confidence"] == 0.8
    provider.close()


def test_vision_text_and_line_limits_truncate(tmp_path) -> None:
    provider, _, _ = _provider(tmp_path, max_text_chars=7, max_lines=10)
    result = provider.invoke(VISION_EXTRACT, {"path": "sample.png"})
    assert result["text"] == "alpha\nb"
    assert result["truncated"] is True
    assert result["line_count"] == 2
    provider.close()


def test_vision_rejects_unbound_paths_extensions_and_controls(tmp_path) -> None:
    provider, _, root = _provider(tmp_path)
    (root / "note.txt").write_text("not an image", encoding="utf-8")
    with pytest.raises(ValueError, match="within"):
        provider.invoke(IMAGE_OCR, {"path": "../sample.png"})
    with pytest.raises(ValueError, match="extension"):
        provider.invoke(IMAGE_OCR, {"path": "note.txt"})
    with pytest.raises(ValueError, match="unsupported vision parameters"):
        provider.invoke(IMAGE_OCR, {"path": "sample.png", "url": "x"})
    provider.close()


def test_vision_symlink_is_not_followed(tmp_path) -> None:
    provider, _, root = _provider(tmp_path)
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"outside")
    link = root / "link.png"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(OSError):
        provider.invoke(IMAGE_OCR, {"path": "link.png"})
    provider.close()


def test_vision_inventory_is_read_only(tmp_path) -> None:
    provider, _, _ = _provider(tmp_path)
    inventory = CapabilityInventory()
    assert register_vision_provider(provider, inventory) == (IMAGE_OCR, VISION_EXTRACT)
    assert inventory.get(IMAGE_OCR).side_effects is SideEffectLevel.READ_ONLY
    assert inventory.get(VISION_EXTRACT).side_effects is SideEffectLevel.READ_ONLY
    provider.close()
