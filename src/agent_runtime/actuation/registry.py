from __future__ import annotations

from .actuator import Actuator
from .command import ActuationCommand
from .result import ActuationResult


class ActuatorRegistry:
    def __init__(self) -> None:
        self._actuators: list[Actuator] = []

    def register(self, actuator: Actuator) -> None:
        self._actuators.append(actuator)

    def all(self) -> list[Actuator]:
        return list(self._actuators)

    def execute(self, command: ActuationCommand) -> ActuationResult:
        for actuator in self._actuators:
            if command.kind in actuator.capabilities():
                return actuator.execute(command)
        return ActuationResult(
            command_id=command.command_id,
            success=False,
            description=f"no actuator for {command.kind}",
        )
