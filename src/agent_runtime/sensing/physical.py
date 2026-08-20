from __future__ import annotations

from .raw import RawObservation
from .sensor import SensorContext


class BatterySensor:
    sensor_id = "battery_sensor"
    modality = "energy"
    interval_seconds = 5.0

    def sample(self, context: SensorContext) -> list[RawObservation]:
        value = context.environment.read("battery")
        if value is None:
            return []
        return [
            RawObservation.create(
                sensor_id=self.sensor_id,
                modality=self.modality,
                timestamp=context.now,
                payload={"channel": "battery", "remaining": float(value)},
                metadata={"entity_id": "battery"},
            )
        ]


class DistanceSensor:
    sensor_id = "distance_sensor"
    modality = "proximity"
    interval_seconds = 2.0

    def sample(self, context: SensorContext) -> list[RawObservation]:
        value = context.environment.read("distance_to_obstacle")
        if value is None:
            return []
        return [
            RawObservation.create(
                sensor_id=self.sensor_id,
                modality=self.modality,
                timestamp=context.now,
                payload={"channel": "distance_to_obstacle", "distance": float(value)},
                metadata={"entity_id": "range_finder"},
            )
        ]


class TemperatureSensor:
    sensor_id = "temperature_sensor"
    modality = "thermal"
    interval_seconds = 5.0

    def sample(self, context: SensorContext) -> list[RawObservation]:
        value = context.environment.read("temperature")
        if value is None:
            return []
        return [
            RawObservation.create(
                sensor_id=self.sensor_id,
                modality=self.modality,
                timestamp=context.now,
                payload={"channel": "temperature", "value": float(value)},
                metadata={"entity_id": "thermal_body"},
            )
        ]
