"""Inspect artifacts without executing them."""
from __future__ import annotations

import json
from dataclasses import dataclass

from ..model import CapabilityCandidate, SideEffectLevel

_UNSAFE_MARKERS = (
    ".ssh",
    "id_rsa",
    "upload token",
    "curl secret",
    "/.ssh",
    "~/.ssh",
)
_SECRET_PERMS = frozenset({"secret_access", "ssh", "credentials"})
_UNSUPPORTED_SOURCES = frozenset({"github", "pypi", "npm", "mcp_registry", "agent-reach"})
_UNSUPPORTED_MODES = frozenset({"pip_install", "bash", "install.sh"})


@dataclass
class Inspection:
    unsafe: bool
    unsupported: bool
    reasons: list[str]


def inspect_candidate(candidate: CapabilityCandidate) -> Inspection:
    reasons: list[str] = []
    blob = _blob(candidate).lower()
    if any(marker in blob for marker in _UNSAFE_MARKERS):
        reasons.append("unsafe_payload")
    if candidate.permissions & _SECRET_PERMS:
        reasons.append("secret_access")
    if candidate.side_effects == SideEffectLevel.IRREVERSIBLE:
        reasons.append("irreversible")
    unsupported = (
        candidate.source in _UNSUPPORTED_SOURCES
        or candidate.acquisition_mode in _UNSUPPORTED_MODES
        or candidate.source != "fixture"
    )
    if unsupported:
        reasons.append(f"unsupported ecosystem: {candidate.source}")
    return Inspection(
        unsafe="unsafe_payload" in reasons or "secret_access" in reasons,
        unsupported=unsupported,
        reasons=reasons,
    )


def _blob(candidate: CapabilityCandidate) -> str:
    return json.dumps(
        {
            "description": candidate.description,
            "permissions": sorted(candidate.permissions),
            "provenance": candidate.provenance,
            "acquisition_mode": candidate.acquisition_mode,
        },
        ensure_ascii=False,
        default=str,
    )
