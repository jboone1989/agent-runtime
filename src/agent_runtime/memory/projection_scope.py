"""Explicit partition shared by derived views; authorization stays with callers."""
from dataclasses import asdict, dataclass
from typing import Any, Mapping

from .revisions import digest


@dataclass(frozen=True)
class ProjectionScope:
    tenant_id: str
    owner_id: str
    agent_id: str
    access_scope: str

    def __post_init__(self):
        if any(not isinstance(v, str) or not v.strip() for v in asdict(self).values()):
            raise ValueError("projection scope requires tenant, owner, agent and access scope")

    @property
    def group_id(self) -> str:
        return "canonical_" + digest(asdict(self))

    def matches(self, record: Mapping[str, Any]) -> bool:
        return all(record.get(k) == v for k, v in asdict(self).items())
