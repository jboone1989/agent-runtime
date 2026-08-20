from __future__ import annotations

from ..records import OutcomeRecord


def outcome_ok(record: OutcomeRecord) -> bool:
    return record.success
