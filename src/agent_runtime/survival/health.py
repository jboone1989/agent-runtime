"""Health monitoring feeds viability; recovery/failover stay thin in v0.1."""
from __future__ import annotations

from ..self_model.model import SelfState


def snapshot_metrics(self_state: SelfState) -> dict[str, float]:
    return dict(self_state.metrics)
