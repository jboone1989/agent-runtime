from __future__ import annotations

import inspect


def test_p2_optional_dependencies_expose_expected_apis() -> None:
    import paddle
    from e2b_code_interpreter import Sandbox
    from faster_whisper import WhisperModel
    from opentelemetry import trace
    from paddleocr import PaddleOCR

    create = inspect.signature(Sandbox.create)
    run_code = inspect.signature(Sandbox.run_code)
    transcribe = inspect.signature(WhisperModel.transcribe)
    predict = inspect.signature(PaddleOCR.predict)

    assert "secure" in create.parameters
    assert "allow_internet_access" in create.parameters
    assert "timeout" in run_code.parameters
    assert "audio" in transcribe.parameters
    assert "task" in transcribe.parameters
    assert "input" in predict.parameters
    assert paddle.__version__
    assert trace.get_tracer("agent_runtime") is not None


def test_public_p2_exports_import() -> None:
    from agent_runtime import OpenTelemetryHooks
    from agent_runtime.integrations import (
        AUDIO_TRANSCRIBE,
        IMAGE_OCR,
        SANDBOX_EXECUTE,
        VISION_EXTRACT,
        E2BCodeInterpreterProvider,
        FasterWhisperProvider,
        PaddleOCRProvider,
    )

    assert OpenTelemetryHooks is not None
    assert E2BCodeInterpreterProvider is not None
    assert FasterWhisperProvider is not None
    assert PaddleOCRProvider is not None
    assert (SANDBOX_EXECUTE, AUDIO_TRANSCRIBE, IMAGE_OCR, VISION_EXTRACT) == (
        "sandbox.execute",
        "audio.transcribe",
        "image.ocr",
        "vision.extract",
    )
