"""Drive names used by DefaultMotivationEngine. Not a chemical model."""
from __future__ import annotations

RESTORE_PREFIX = "restore_"


def restore_drive(dimension: str) -> str:
    return f"{RESTORE_PREFIX}{dimension}"
