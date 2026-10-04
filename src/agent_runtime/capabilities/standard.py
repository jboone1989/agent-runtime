"""Stable provider-neutral capability identifiers.

These IDs describe *what* an agent needs, never which library or language
implements it.  Concrete providers are deployment choices.
"""
from __future__ import annotations

MEMORY_CAPABILITY_ID = "agent.memory"
LEARNING_CAPABILITY_ID = "agent.learning"
BRAIN_CAPABILITY_ID = "agent.brain"
RESEARCH_CAPABILITY_ID = "agent.research"
BROWSER_CAPABILITY_ID = "agent.browser"
CHANNEL_CAPABILITY_PREFIX = "channel."

__all__ = [
    "MEMORY_CAPABILITY_ID",
    "LEARNING_CAPABILITY_ID",
    "BRAIN_CAPABILITY_ID",
    "RESEARCH_CAPABILITY_ID",
    "BROWSER_CAPABILITY_ID",
    "CHANNEL_CAPABILITY_PREFIX",
]
