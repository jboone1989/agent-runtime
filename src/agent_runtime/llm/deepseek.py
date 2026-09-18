"""DeepSeek Anthropic-compatible provider with environment-only credentials."""
from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any


class LLMProviderError(RuntimeError):
    """A provider call failed without exposing credentials or response bodies."""


@dataclass
class DeepSeekAnthropicProvider:
    api_key: str = field(repr=False)
    model: str
    base_url: str = "https://api.deepseek.com/anthropic"
    timeout_seconds: float = 180.0
    last_metadata: dict[str, Any] = field(default_factory=dict, init=False, repr=False)

    @classmethod
    def from_environment(cls) -> DeepSeekAnthropicProvider:
        api_key = _environment_value("DEEPSEEK_API_KEY")
        if not api_key:
            raise LLMProviderError("DEEPSEEK_API_KEY is not configured")
        model = _environment_value("DEEPSEEK_MODEL")
        if not model:
            from ..config.loader import load_config

            model = load_config().llm.model
        return cls(
            api_key=api_key,
            model=model,
            base_url=_environment_value("DEEPSEEK_BASE_URL")
            or "https://api.deepseek.com/anthropic",
        )

    async def complete(self, prompt: str, **kwargs: Any) -> str:
        return await asyncio.to_thread(self._complete_sync, prompt, kwargs)

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
            raise LLMProviderError("provider returned invalid structured JSON") from exc
        if not isinstance(value, dict):
            raise LLMProviderError("provider returned a non-object JSON value")
        return value

    async def embed(self, text: str, **kwargs: Any) -> list[float]:
        _ = (text, kwargs)
        raise LLMProviderError("DeepSeek Anthropic endpoint does not provide embeddings")

    def _complete_sync(self, prompt: str, kwargs: dict[str, Any]) -> str:
        thinking_enabled = bool(kwargs.get("thinking", False))
        body = {
            "model": str(kwargs.get("model") or self.model),
            "max_tokens": int(kwargs.get("max_tokens", 1024)),
            "thinking": {"type": "enabled" if thinking_enabled else "disabled"},
            "system": str(
                kwargs.get("system")
                or "You are a bounded cognitive service for a persistent agent runtime."
            ),
            "messages": [{"role": "user", "content": prompt}],
        }
        request = urllib.request.Request(
            self.base_url.rstrip("/") + "/v1/messages",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "content-type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self.timeout_seconds
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise LLMProviderError(f"DeepSeek HTTP error {exc.code}") from exc
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise LLMProviderError("DeepSeek request failed") from exc
        usage = payload.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        self.last_metadata = {
            "prompt_tokens": _nonnegative_int(
                usage.get("input_tokens") if "input_tokens" in usage else usage.get("prompt_tokens")
            ),
            "completion_tokens": _nonnegative_int(
                usage.get("output_tokens") if "output_tokens" in usage else usage.get("completion_tokens")
            ),
            "cost": _nonnegative_float(usage.get("cost")),
        }
        content = payload.get("content", [])
        text = "".join(
            str(item.get("text") or "")
            for item in content
            if item.get("type") == "text"
        ).strip()
        if not text:
            content_types = sorted(
                {str(item.get("type") or "unknown") for item in content}
            )
            stop_reason = str(payload.get("stop_reason") or "unknown")
            types = ",".join(content_types) or "none"
            raise LLMProviderError(
                "DeepSeek returned no text "
                f"(stop_reason={stop_reason}, content_types={types})"
            )
        return text


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _nonnegative_float(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return max(0.0, float(value))
    return None


def _environment_value(name: str) -> str | None:
    value = os.environ.get(name)
    if value:
        return value
    if os.name != "nt":
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, name)
    except (FileNotFoundError, OSError):
        return None
    return str(value) if value else None


def _json_object(text: str) -> str:
    stripped = text.strip()
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
