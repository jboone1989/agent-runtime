from __future__ import annotations

"""Authorized replication only. v0.1 never auto-executes this."""


class ReplicationController:
    def replica_allowed(self, may_spawn: bool, human_approved: bool) -> bool:
        return may_spawn and human_approved
