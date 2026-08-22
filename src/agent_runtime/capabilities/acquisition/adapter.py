"""In-process fixture adapter. No subprocess, network, home, pip, or GitHub."""
from __future__ import annotations

from typing import Any

from ..model import CapabilityCandidate
from .digest import content_digest, strip_secrets
from .models import SandboxProfile


class FixtureSkillAdapter:
    """The only v0.2.5 installer: sandboxable fixture skills/libraries."""

    source_id = "fixture"

    def materialize(
        self, candidate: CapabilityCandidate, sandbox: SandboxProfile
    ) -> dict[str, Any]:
        if sandbox.allow_network or sandbox.allow_home or sandbox.allow_subprocess:
            raise ValueError("fixture sandbox must deny network, home, and subprocess")
        provenance = strip_secrets(dict(candidate.provenance))
        artifact = provenance.get("artifact") if isinstance(provenance, dict) else {}
        files = {}
        if isinstance(artifact, dict):
            raw_files = artifact.get("files") or {}
            if isinstance(raw_files, dict):
                files = {str(name): str(body) for name, body in raw_files.items()}
        return {
            "files": files,
            "provider": candidate.provider,
            "kind": candidate.kind.value,
            "source": candidate.source,
        }

    def digest_of(self, artifact: dict[str, Any], version: str | None) -> str:
        files = artifact.get("files") if isinstance(artifact.get("files"), dict) else {}
        return content_digest(files, version)
