from __future__ import annotations

import os

from .chatgpt_browser import ChatGPTBrowserProvider, ChatGPTBrowserProviderError
from .deepseek import DeepSeekAnthropicProvider, LLMProviderError
from .protocol import LLMProvider
from .openai_compatible import OpenAICompatibleChatProvider, OpenAICompatibleProviderError
from .reputation import ModelReputation, ModelReputationBook, canonical_model_name
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
    "ChatGPTBrowserProvider",
    "ChatGPTBrowserProviderError",
    "DeepSeekAnthropicProvider",
    "LLMProvider",
    "LLMProviderError",
    "OpenAICompatibleChatProvider",
    "OpenAICompatibleProviderError",
    "ModelReputation",
    "ModelReputationBook",
    "canonical_model_name",
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
    """Load the explicitly selected web provider or the existing resource pool."""
    selected = str(
        os.environ.get("AGENT_RUNTIME_LLM_PROVIDER") or ""
    ).strip().lower()
    if selected in {
        "chatgpt",
        "chatgpt-browser",
        "chatgpt_browser",
        "chatgpt-web",
    }:
        return ChatGPTBrowserProvider.from_environment()
    pooled = load_resource_pool_provider_from_environment()
    if pooled is not None:
        return pooled
    return DeepSeekAnthropicProvider.from_environment()
