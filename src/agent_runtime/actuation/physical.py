"""Mock physical actuators. Core runtime does not know these are physical."""
from __future__ import annotations

from ..environment.base import EnvironmentAdapter
from .command import ActuationCommand
from .result import ActuationResult


class MovementActuator:
    actuator_id = "movement_actuator"

    def __init__(self, environment: EnvironmentAdapter) -> None:
        self.environment = environment

    def capabilities(self) -> set[str]:
        return {"move"}

    def execute(self, command: ActuationCommand) -> ActuationResult:
        delta = float(command.payload.get("delta", 0.05))
        distance = float(self.environment.read("distance_to_obstacle") or 0.0)
        self.environment.apply("distance_to_obstacle", min(1.0, max(0.0, distance + delta)))
        return ActuationResult(command.command_id, True, "moved", {"delta": delta})


class CoolingActuator:
    actuator_id = "cooling_actuator"

    def __init__(self, environment: EnvironmentAdapter) -> None:
        self.environment = environment

    def capabilities(self) -> set[str]:
        return {"cool"}

    def execute(self, command: ActuationCommand) -> ActuationResult:
        temp = float(self.environment.read("temperature") or 0.0)
        cooled = max(0.0, temp - 0.1)
        self.environment.apply("temperature", cooled)
        return ActuationResult(command.command_id, True, "cooled", {"temperature": cooled})


class PhysicalConserveActuator:
    actuator_id = "physical_conserve_actuator"

    def __init__(self, environment: EnvironmentAdapter, regulatory_state) -> None:
        self.environment = environment
        self.regulatory_state = regulatory_state

    def capabilities(self) -> set[str]:
        return {"conserve"}

    def execute(self, command: ActuationCommand) -> ActuationResult:
        self.environment.apply("conservation", True)
        if self.regulatory_state is not None:
            self.regulatory_state.resource_conservation = max(
                float(self.regulatory_state.resource_conservation), 0.7
            )
        return ActuationResult(command.command_id, True, "conserved", {"conservation": True})
