from __future__ import annotations


class ProceduralMemory:
    def __init__(self) -> None:
        self.procedures: dict[str, str] = {}

    def remember(self, name: str, body: str) -> None:
        self.procedures[name] = body
