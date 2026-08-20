from __future__ import annotations

from dataclasses import dataclass


@dataclass
class InformationGoal:
    question: str
    expected_information_gain: float


@dataclass
class ActivePerceptionAction:
    sensor_id: str
    command: str
