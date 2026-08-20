from __future__ import annotations

from typing import Any


class SemanticMemory:
    def __init__(self) -> None:
        self.facts: dict[str, Any] = {}

    def put(self, key: str, value: Any) -> None:
        self.facts[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self.facts.get(key, default)
