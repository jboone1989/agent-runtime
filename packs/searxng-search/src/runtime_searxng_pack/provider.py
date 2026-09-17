"""SearXNG pack re-exports Runtime's canonical web.search provider.

The capability pack owns packaging and deployment configuration only. Contract,
security filtering, fallback behavior and normalization live in Agent Runtime so
they cannot drift between direct and pack-backed invocation paths.
"""
from agent_runtime.integrations.web_search import WEB_SEARCH, SearXNGSearchProvider

__all__ = ["SearXNGSearchProvider", "WEB_SEARCH"]
