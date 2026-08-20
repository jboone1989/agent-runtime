"""A persistent runtime for autonomous agents."""

from __future__ import annotations

from .motivation.default import DefaultMotivationEngine
from .motivation.engine import MotivationEngine
from .runtime import AgentRuntime

__version__ = "0.1.0"

__all__ = [
    "AgentRuntime",
    "DefaultMotivationEngine",
    "MotivationEngine",
    "__version__",
]
