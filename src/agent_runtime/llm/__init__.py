from __future__ import annotations

from .deepseek import DeepSeekAnthropicProvider, LLMProviderError
from .protocol import LLMProvider

__all__ = [
    "DeepSeekAnthropicProvider",
    "LLMProvider",
    "LLMProviderError",
    "load_llm_provider",
]


def load_llm_provider() -> LLMProvider:
    """Business agents depend on this factory, not a named vendor class."""
    return DeepSeekAnthropicProvider.from_environment()
