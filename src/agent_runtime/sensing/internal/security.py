"""Defensive sensing of the agent's own environment. No external scanning."""
from __future__ import annotations

from ..sensor import SensorContext
from ..signal import Signal


class SecuritySensor:
    name = "security_sensor"
    interval_seconds = 10.0

    def sense(self, context: SensorContext) -> list[Signal]:
        now = context.now
        sec = context.world.security
        mutated = bool(sec.get("config_mutated", False))
        creds = bool(sec.get("credentials_valid", True))
        sig = bool(sec.get("signature_ok", True))
        dep = bool(sec.get("dependency_hash_ok", True))
        auth = bool(sec.get("auth_anomaly", False))
        return [
            Signal.create(
                source=self.name,
                domain="integrity",
                kind="CONFIG_MUTATION",
                value=mutated,
                severity=0.85 if mutated else 0.0,
                confidence=0.95,
                scope="SELF",
                timestamp=now,
            ),
            Signal.create(
                source=self.name,
                domain="integrity",
                kind="CREDENTIAL_INVALID",
                value=not creds,
                severity=0.9 if not creds else 0.0,
                confidence=0.95,
                scope="SELF",
                timestamp=now,
            ),
            Signal.create(
                source=self.name,
                domain="integrity",
                kind="SIGNATURE_FAILURE",
                value=not sig,
                severity=0.8 if not sig else 0.0,
                confidence=0.9,
                scope="SELF",
                timestamp=now,
            ),
            Signal.create(
                source=self.name,
                domain="integrity",
                kind="DEPENDENCY_HASH",
                value=not dep,
                severity=0.7 if not dep else 0.0,
                confidence=0.85,
                scope="SELF",
                timestamp=now,
            ),
            Signal.create(
                source=self.name,
                domain="integrity",
                kind="AUTH_ANOMALY",
                value=auth,
                severity=0.6 if auth else 0.0,
                confidence=0.7,
                scope="SELF",
                timestamp=now,
            ),
        ]
