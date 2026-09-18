from __future__ import annotations

from .deepseek import DeepSeekAnthropicProvider, LLMProviderError
from .protocol import LLMProvider
from .openai_compatible import OpenAICompatibleChatProvider, OpenAICompatibleProviderError
from .resource_pool import (
    LLMResourceHealth,
    LLMResourceSpec,
    LLMRoutingDecision,
    LLMRoutingPolicy,
    ResourcePoolProvider,
    load_resource_pool_manifest,
    load_resource_pool_provider_from_environment,
)

__all__ = [
    "DeepSeekAnthropicProvider",
    "LLMProvider",
    "LLMProviderError",
    "OpenAICompatibleChatProvider",
    "OpenAICompatibleProviderError",
    "LLMResourceHealth",
    "LLMResourceSpec",
    "LLMRoutingDecision",
    "LLMRoutingPolicy",
    "ResourcePoolProvider",
    "load_resource_pool_manifest",
    "load_resource_pool_provider_from_environment",
    "load_llm_provider",
]


def load_llm_provider() -> LLMProvider:
    """Load the resource pool when configured, otherwise preserve stable DeepSeek fallback."""
    pooled = load_resource_pool_provider_from_environment()
    if pooled is not None:
        return pooled
    return DeepSeekAnthropicProvider.from_environment()
