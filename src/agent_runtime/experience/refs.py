"""Stable causal refs. Free-text reason is not the only causal link."""
from __future__ import annotations

import hashlib


def stable_ref(prefix: str, *parts: object) -> str:
    raw = "|".join("" if part is None else str(part) for part in parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"
