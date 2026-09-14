# Generic capability integrations — P2

This batch adds useful but non-foundational capabilities without creating a second planner, registry, memory system, or authority model. Runtime remains responsible for side-effect ceilings, permissions, receipts, persistence boundaries, and provider lifecycle.

## `sandbox.execute`

Provider: `E2BCodeInterpreterProvider` using `e2b-code-interpreter`.

The v1 contract executes Python cells only. It does not expose shell commands, arbitrary process execution, request-supplied environment variables, file upload controls, or provider template controls. Each invocation uses a fresh secure E2B sandbox and destroys it after execution. Network access is disabled by default and can only be enabled in deployment policy, not per request.

`sandbox.execute` is classified as `EXTERNAL_SIDE_EFFECT` and requires the `sandbox.execute` Runtime permission. Source code length, output length, and execution timeout are bounded by Runtime policy.

Install with:

```bash
pip install -e '.[sandbox]'
```

## `audio.transcribe`

Provider: `FasterWhisperProvider` using `faster-whisper`.

Audio is read only from explicitly bound Runtime roots using the same pinned-root and no-symlink traversal rules used by document integrations. Input bytes, transcript characters, and segment count are bounded.

The provider does not silently acquire a model during invocation. Deployment must either inject an already-created Whisper model or bind a local model path loaded with `local_files_only=True`.

Install with:

```bash
pip install -e '.[audio]'
```

## `image.ocr` / `vision.extract`

Provider: `PaddleOCRProvider` using PaddleOCR 3.x with a local PaddlePaddle inference runtime.

Both capabilities are `READ_ONLY`. Images must live below configured roots and use an allowlisted image extension. Input bytes, result objects, line count, and emitted text are bounded. `image.ocr` returns text/confidence; `vision.extract` additionally returns bounded boxes when the provider supplies them.

Model construction is an explicit deployment-time action. Invocation receives an already configured PaddleOCR engine and does not trigger hidden model acquisition.

Install with:

```bash
pip install -e '.[vision]'
```

The `vision` extra includes both `paddleocr` and the CPU `paddlepaddle` runtime. GPU deployments should manage the compatible PaddlePaddle GPU wheel at the deployment layer.

## OpenTelemetry hooks

`OpenTelemetryHooks` is platform instrumentation, not a model-facing capability. `ApplicationRuntime` accepts an optional telemetry implementation and otherwise uses a no-op hook.

Invocation spans contain only bounded metadata: capability ID, provider ID, side-effect level, success flag, and exception type. Request payloads, result payloads, document/audio/image contents, credentials, and exception messages are not exported. Automatic OpenTelemetry exception recording is explicitly disabled so provider exception messages cannot bypass this boundary.

Install with:

```bash
pip install -e '.[telemetry]'
```

## Acceptance boundaries

All P2 providers keep provider-neutral capability IDs and register into the existing Capability Fabric / `ApplicationRuntime` path. No provider can self-elevate authority. Optional dependencies remain outside Runtime core, and tests cover permission gates, path isolation, output budgets, provider API compatibility, and telemetry redaction behavior.
