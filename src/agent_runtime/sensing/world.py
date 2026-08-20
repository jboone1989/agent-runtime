"""Injectable environment that sensors read. Tests mutate this; sensors never write SelfModel."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _default_providers() -> dict[str, dict[str, Any]]:
    healthy = {"error_rate": 0.02, "latency_ms": 80.0, "available": True, "rate_limited": False}
    return {"primary": dict(healthy), "backup": dict(healthy)}


@dataclass
class SenseWorld:
    resources: dict[str, float] = field(
        default_factory=lambda: {
            "compute": 0.80,
            "memory": 0.80,
            "disk": 0.80,
            "token_budget": 0.80,
            "financial": 0.70,
        }
    )
    process: dict[str, Any] = field(
        default_factory=lambda: {
            "alive": True,
            "worker_health": 0.95,
            "queue_backlog": 0,
            "crash_count": 0,
            "restart_count": 0,
        }
    )
    providers: dict[str, dict[str, Any]] = field(default_factory=_default_providers)
    memory: dict[str, Any] = field(
        default_factory=lambda: {
            "retrieval_failure": 0.0,
            "corruption": 0.0,
            "read_latency": 0.05,
            "write_failure": 0.0,
            "consistency": 1.0,
        }
    )
    security: dict[str, Any] = field(
        default_factory=lambda: {
            "config_mutated": False,
            "credentials_valid": True,
            "signature_ok": True,
            "dependency_hash_ok": True,
            "auth_anomaly": False,
        }
    )
    network: dict[str, Any] = field(
        default_factory=lambda: {"latency_ms": 40.0, "available": True}
    )
