"""Temporary tendency-to-tool mapping. Not part of contract v1."""
from __future__ import annotations

from typing import Any

from ..motivation.engine import ActionTendency

CONTRACT_ACTION_TO_KIND = {
    "REST": "conserve",
    "APPROACH": "maintain",
    "AVOID": "conserve",
    "EXPLORE": "introspect",
    "CONFRONT": "diagnose",
    "SEEK_SUPPORT": "record",
    "FREEZE": "noop",
}


def action_tendencies_from_contract(items: list[dict[str, Any]]) -> list[ActionTendency]:
    out: list[ActionTendency] = []
    for item in items:
        direction = str(item.get("direction") or "EXPLORE")
        kind = CONTRACT_ACTION_TO_KIND.get(direction, "introspect")
        out.append(
            ActionTendency(
                action=kind,
                strength=float(item.get("strength") or 0.0),
                reason=f"legacy:{direction}",
                dimension=None,
            )
        )
    return out
