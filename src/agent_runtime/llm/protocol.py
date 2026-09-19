"""LLM provider protocol. Model id is never a business-layer constant."""
from __future__ import annotations

from typing import Any, AsyncIterator, Protocol


class LLMProvider(Protocol):
    async def stream(self, prompt: str, **kwargs: Any) -> AsyncIterator[str]: ...

    async def complete(self, prompt: str, **kwargs: Any) -> str: ...

    async def structured(self, prompt: str, schema: dict[str, Any], **kwargs: Any) -> dict[str, Any]: ...

    async def embed(self, text: str, **kwargs: Any) -> list[float]: ...
