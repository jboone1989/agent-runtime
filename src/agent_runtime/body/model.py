from __future__ import annotations

from dataclasses import dataclass, field

from .boundary import BoundaryRelation, SelfBoundary
from .component import BodyComponent


@dataclass
class BodyModel:
    components: dict[str, BodyComponent] = field(default_factory=dict)
    boundaries: list[SelfBoundary] = field(default_factory=list)

    def register(self, component: BodyComponent, boundary: SelfBoundary) -> None:
        self.components[component.component_id] = component
        self.boundaries.append(boundary)

    def get(self, component_id: str) -> BodyComponent | None:
        return self.components.get(component_id)

    def relation_of(self, entity_id: str) -> BoundaryRelation:
        for boundary in self.boundaries:
            if boundary.entity_id == entity_id:
                return boundary.relation
        return BoundaryRelation.UNKNOWN

    def dependents_of(self, component_id: str) -> set[str]:
        found = {component_id}
        changed = True
        while changed:
            changed = False
            for cid, component in self.components.items():
                if cid in found:
                    continue
                if any(dep in found for dep in component.dependencies):
                    found.add(cid)
                    changed = True
        return found

    def containment_boundary(self, component_id: str) -> set[str]:
        """Minimum region: the component itself, not the whole body."""
        component = self.components.get(component_id)
        if component is None:
            return {component_id}
        return {component_id}

    @classmethod
    def empty(cls) -> BodyModel:
        return cls()
