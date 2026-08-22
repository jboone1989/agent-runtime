"""Joint goals reference independent participant goals. They do not swallow them."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class JointGoalStatus(str, Enum):
    ACTIVE = "ACTIVE"
    DISSOLVED = "DISSOLVED"


@dataclass
class JointGoal:
    joint_id: str
    description: str
    local_goal_id: str
    other_id: str
    other_goal_id: str
    status: JointGoalStatus = JointGoalStatus.ACTIVE
