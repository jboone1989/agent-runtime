"""Preferred vs critical bands per dimension (not a single reward)."""
from __future__ import annotations

from ..config.loader import ViabilityConfig
from .state import ViabilityDimension


def bounds_for(cfg: ViabilityConfig, name: str) -> tuple[float, float, float, float]:
    b = cfg.dimensions[name]
    return b.preferred_min, b.preferred_max, b.critical_min, b.critical_max


def in_preferred(dim: ViabilityDimension) -> bool:
    return dim.preferred_min <= dim.value <= dim.preferred_max


def in_critical(dim: ViabilityDimension) -> bool:
    return dim.value < dim.critical_min or dim.value > dim.critical_max
