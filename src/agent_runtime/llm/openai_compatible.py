"""Minimal OpenAI-compatible chat provider with environment-only credentials."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Mapping
import urllib.error
import urllib.request


class OpenAICompatibleProviderError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = True) -> None:
        super().__init__(code)
        self.code = str(code)
        self.retryable = bool(retryable)


@dataclass
class OpenAICompatibleChatProvider:
    api_key: str = field(repr=False)
    model: str
    base_url: str
    timeout_seconds: float = 120.0
    extra_headers: dict[str, str] = field(default_factory=dict)
    include_usage: bool = True
    last_metadata: dict[str, Any] = field(default_factory=dict, init=False, repr=False)

    async def complete(self, prompt: str, **kwargs: Any) -> str:
        return await asyncio.to_thread(self._complete_sync, prompt, kwargs)

    async def stream(self, prompt: str, **kwargs: Any) -> AsyncIterator[str]:
        body: dict[str, Any] = {
            "model": str(kwargs.get("model") or self.model),
            "messages": [
                *(
                    [{"role": "system", "content": str(kwargs["system"])}]
                    if kwargs.get("system")
                    else []
                ),
                {"role": "user", "content": prompt},
            ],
            "max_tokens": int(kwargs.get("max_tokens", 1024)),
            "temperature": float(kwargs.get("temperature", 0.2)),
            "stream": True,
        }
        request = urllib.request.Request(
            self.base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
                **self.extra_headers,
            },
            method="POST",
        )
        timeout_seconds = max(
            0.1, float(kwargs.get("timeout_seconds") or self.timeout_seconds)
        )
        try:
            response = await asyncio.to_thread(
                urllib.request.urlopen, request, timeout=timeout_seconds
            )
        except urllib.error.HTTPError as exc:
            raise OpenAICompatibleProviderError(
                f"HTTP_{exc.code}", retryable=exc.code not in {400, 401, 403}
            ) from exc
        except OSError as exc:
            raise OpenAICompatibleProviderError("REQUEST_FAILED") from exc

        yielded = False
        usage: dict[str, Any] = {}
        try:
            while True:
                raw_line = await asyncio.to_thread(response.readline)
                if not raw_line:
                    break
                line = raw_line.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    if data == "[DONE]":
                        break
                    continue
                try:
                    payload = json.loads(data)
                except (ValueError, json.JSONDecodeError) as exc:
                    raise OpenAICompatibleProviderError("INVALID_STREAM_EVENT") from exc
                if not isinstance(payload, Mapping):
                    continue
                raw_usage = payload.get("usage")
                if isinstance(raw_usage, Mapping):
                    usage = dict(raw_usage)
                choices = payload.get("choices")
                if not isinstance(choices, list) or not choices:
                    continue
                choice = choices[0]
                if not isinstance(choice, Mapping):
                    continue
                delta = choice.get("delta")
                if not isinstance(delta, Mapping):
                    continue
                text = _stream_delta_text(delta.get("content"))
                if text:
                    yielded = True
                    yield text
        finally:
            await asyncio.to_thread(response.close)

        self.last_metadata = {
            "prompt_tokens": _nonnegative_int(usage.get("prompt_tokens")),
            "completion_tokens": _nonnegative_int(usage.get("completion_tokens")),
            "cost": _nonnegative_float(usage.get("cost")),
        }
        if not yielded:
            raise OpenAICompatibleProviderError("EMPTY_RESPONSE")

    async def structured(
        self, prompt: str, schema: dict[str, Any], **kwargs: Any
    ) -> dict[str, Any]:
        instruction = (
            f"{prompt}\n\nReturn only one JSON object matching this JSON schema:\n"
            f"{json.dumps(schema, ensure_ascii=False)}"
        )
        raw = await self.complete(instruction, **kwargs)
        try:
            value = json.loads(_json_object(raw))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise OpenAICompatibleProviderError(
                "INVALID_STRUCTURED_JSON", retryable=False
            ) from exc
        if not isinstance(value, dict):
            raise OpenAICompatibleProviderError(
                "NON_OBJECT_STRUCTURED_JSON", retryable=False
            )
        return value

    async def embed(self, text: str, **kwargs: Any) -> list[float]:
        _ = (text, kwargs)
        raise OpenAICompatibleProviderError("EMBEDDINGS_UNSUPPORTED", retryable=False)

    def _complete_sync(self, prompt: str, kwargs: dict[str, Any]) -> str:
        body: dict[str, Any] = {
            "model": str(kwargs.get("model") or self.model),
            "messages": [
                *(
                    [{"role": "system", "content": str(kwargs["system"])}]
                    if kwargs.get("system")
                    else []
                ),
                {"role": "user", "content": prompt},
            ],
            "max_tokens": int(kwargs.get("max_tokens", 1024)),
            "temperature": float(kwargs.get("temperature", 0.2)),
            "stream": False,
        }
        if self.include_usage:
            body["usage"] = {"include": True}
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            **self.extra_headers,
        }
        request = urllib.request.Request(
            self.base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        timeout_seconds = max(
            0.1, float(kwargs.get("timeout_seconds") or self.timeout_seconds)
        )
        try:
            with urllib.request.urlopen(
                request, timeout=timeout_seconds
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            code = f"HTTP_{exc.code}"
            raise OpenAICompatibleProviderError(
                code, retryable=exc.code not in {400, 401, 403}
            ) from exc
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise OpenAICompatibleProviderError("REQUEST_FAILED") from exc
        if not isinstance(payload, Mapping):
            raise OpenAICompatibleProviderError("INVALID_RESPONSE")
        choices = payload.get("choices")
        if (
            not isinstance(choices, list)
            or not choices
            or not isinstance(choices[0], Mapping)
        ):
            raise OpenAICompatibleProviderError("MISSING_CHOICES")
        message = choices[0].get("message")
        if not isinstance(message, Mapping):
            raise OpenAICompatibleProviderError("MISSING_MESSAGE")
        text = str(message.get("content") or "").strip()
        if not text:
            raise OpenAICompatibleProviderError("EMPTY_RESPONSE")
        usage = payload.get("usage")
        usage = dict(usage) if isinstance(usage, Mapping) else {}
        self.last_metadata = {
            "prompt_tokens": _nonnegative_int(usage.get("prompt_tokens")),
            "completion_tokens": _nonnegative_int(usage.get("completion_tokens")),
            "cost": _nonnegative_float(usage.get("cost")),
        }
        return text


def _stream_delta_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, Mapping):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _nonnegative_float(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return max(0.0, float(value))
    return None


def _json_object(text: str) -> str:
    stripped = str(text).strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end < start:
        raise ValueError("JSON object not found")
    return stripped[start : end + 1]


__all__ = ["OpenAICompatibleChatProvider", "OpenAICompatibleProviderError"]
