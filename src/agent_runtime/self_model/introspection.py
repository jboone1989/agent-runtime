"""Structured introspection. Language is a report, not the source of truth."""
from __future__ import annotations

from .model import SelfModel


def describe_self(model: SelfModel) -> str:
    caps = ", ".join(
        f"{n}={'up' if c.available else 'down'}(u={c.uncertainty:.2f})"
        for n, c in sorted(model.capabilities.items())
    )
    states = ", ".join(
        f"{n}={b.value}(u={b.uncertainty:.2f})"
        for n, b in sorted(model.internal_state.items())
    )
    return (
        f"agent {model.identity.agent_id} v{model.identity.version}; "
        f"capabilities: {caps or 'none'}; internal: {states or 'unknown'}"
    )


def uncertain_items(model: SelfModel, threshold: float = 0.3) -> dict[str, float]:
    return {k: v for k, v in model.uncertainty.items() if v >= threshold}
