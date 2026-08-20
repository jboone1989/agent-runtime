"""Affect fields on MotivationState. Runtime stores valence/arousal, not PAD theory."""
from __future__ import annotations

from .engine import MotivationState


def affect_tuple(state: MotivationState) -> tuple[float, float, float]:
    return state.valence, state.arousal, state.urgency
