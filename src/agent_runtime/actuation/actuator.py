from __future__ import annotations

from typing import Protocol

from .command import ActuationCommand
from .result import ActuationResult


class Actuator(Protocol):
    actuator_id: str

    def capabilities(self) -> set[str]: ...

    def execute(self, command: ActuationCommand) -> ActuationResult: ...
