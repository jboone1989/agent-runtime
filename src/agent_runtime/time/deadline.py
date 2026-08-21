"""Deadlines attached to waiting or intentions."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class Deadline:
    at: datetime
    kind: str
