"""Habit bindings. Not hardcoded action shortcuts."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class HabitBinding:
    habit_id: str
    fragment: str
    successes: int
    bound: bool
