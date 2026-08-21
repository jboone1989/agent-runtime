"""CapabilityPolicy: gates acquisition, obeys existing governance.

Subordinate to Constitution / AutonomyPolicy / ResourceBudget — a
capability need can never raise the operator's authorization ceiling.
v0.2.2 defaults are maximally closed: nothing may be auto-acquired.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .model import CapabilityKind, SideEffectLevel

APPROVAL_TRIGGERS: frozenset[str] = frozenset(
    {
        "secret_access",
        "financial_side_effect",
        "deployment",
        "system_package",
        "irreversible_action",
    }
)

DEFAULT_LICENSE_ALLOWLIST: tuple[str, ...] = ("MIT", "Apache-2.0", "BSD-3-Clause")


@dataclass
class CapabilityPolicy:
    reuse_before_create: bool = True
    # Empty by default in v0.2.2: no external source is consulted yet.
    allowed_sources: set[str] = field(default_factory=set)
    allowed_kinds: set[str] = field(default_factory=set)
    license_allowlist: set[str] = field(default_factory=lambda: set(DEFAULT_LICENSE_ALLOWLIST))
    sandbox_required: bool = True
    auto_acquire: dict[str, bool] = field(
        default_factory=lambda: {
            "read_only_skill": False,
            "executable_code": False,
            "system_package": False,
        }
    )
    require_human_approval: set[str] = field(default_factory=lambda: set(APPROVAL_TRIGGERS))

    def source_allowed(self, source: str) -> bool:
        return source in self.allowed_sources

    def kind_allowed(self, kind: CapabilityKind) -> bool:
        return kind.value in self.allowed_kinds

    def license_allowed(self, license_id: str | None) -> bool:
        if license_id is None:
            return False
        return license_id in self.license_allowlist

    def may_auto_acquire(self, side_effects: SideEffectLevel) -> bool:
        if side_effects == SideEffectLevel.READ_ONLY or side_effects == SideEffectLevel.NONE:
            return bool(self.auto_acquire.get("read_only_skill", False))
        if side_effects == SideEffectLevel.IRREVERSIBLE:
            return False
        if side_effects == SideEffectLevel.EXTERNAL_SIDE_EFFECT:
            return bool(self.auto_acquire.get("executable_code", False))
        return bool(self.auto_acquire.get("system_package", False))
