"""Uncertainty helpers — SelfModel must expose what it might be wrong about."""
from __future__ import annotations


def default_uncertainty(observation_count: int) -> float:
    if observation_count <= 0:
        return 1.0
    return max(0.05, min(1.0, 1.0 / (1.0 + observation_count)))


def merge_uncertainty(prior: float, likelihood_uncertainty: float) -> float:
    return max(0.0, min(1.0, 0.5 * prior + 0.5 * likelihood_uncertainty))
