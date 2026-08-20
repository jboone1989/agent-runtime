from __future__ import annotations

"""Recovery is a v0.2 capability. Interface only in v0.1."""


class RecoveryController:
    def can_recover(self, dimension: str) -> bool:
        return dimension in {"compute", "memory_integrity", "network_connectivity"}
