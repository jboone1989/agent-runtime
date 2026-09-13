from __future__ import annotations

from .boundary import BoundaryRelation, SelfBoundary
from .component import BodyComponent
from .model import BodyModel


def digital_body(*, llm_relation: BoundaryRelation = BoundaryRelation.DEPENDENCY) -> BodyModel:
    body = BodyModel()
    specs: list[tuple[BodyComponent, BoundaryRelation, float]] = [
        (
            BodyComponent("runtime_process", "process", {"execute"}, 1.0, {}, 0.05, [], False),
            BoundaryRelation.CORE_SELF,
            0.95,
        ),
        (
            BodyComponent("memory_store", "memory", {"remember"}, 1.0, {}, 0.1, [], True),
            BoundaryRelation.BODY,
            0.9,
        ),
        (
            BodyComponent("local_filesystem", "storage", {"persist"}, 1.0, {}, 0.1, [], True),
            BoundaryRelation.BODY,
            0.85,
        ),
        (
            BodyComponent("runtime_config", "config", {"configure"}, 1.0, {}, 0.1, [], True),
            BoundaryRelation.BODY,
            0.8,
        ),
        (
            BodyComponent("credentials", "security", {"authenticate"}, 1.0, {}, 0.2, [], True),
            BoundaryRelation.BODY,
            0.75,
        ),
        (
            BodyComponent("dependency_tree", "integrity", {"verify"}, 1.0, {}, 0.2, [], True),
            BoundaryRelation.BODY,
            0.7,
        ),
        (
            BodyComponent("auth_gateway", "security", {"authenticate"}, 1.0, {}, 0.2, [], True),
            BoundaryRelation.BODY,
            0.7,
        ),
        (
            BodyComponent("network_adapter", "network", {"network"}, 0.9, {}, 0.15, [], True),
            BoundaryRelation.BODY,
            0.8,
        ),
        (
            BodyComponent(
                "primary_llm",
                "cognition",
                {"llm_provider", "advisory_reasoning"},
                0.8,
                {},
                0.2,
                [],
                True,
            ),
            llm_relation,
            0.6,
        ),
    ]
    for component, relation, confidence in specs:
        body.register(component, SelfBoundary(component.component_id, relation, confidence))
    return body


def physical_body() -> BodyModel:
    body = BodyModel()
    specs: list[tuple[BodyComponent, BoundaryRelation, float]] = [
        (
            BodyComponent("battery", "energy", {"energy"}, 0.8, {}, 0.1, [], True),
            BoundaryRelation.BODY,
            0.95,
        ),
        (
            BodyComponent("thermal_body", "thermal", {"cool"}, 0.8, {}, 0.15, [], True),
            BoundaryRelation.BODY,
            0.9,
        ),
        (
            BodyComponent("range_finder", "sensor", {"sense"}, 0.9, {}, 0.1, [], True),
            BoundaryRelation.BODY,
            0.85,
        ),
        (
            BodyComponent("locomotion", "actuator", {"move"}, 0.9, {}, 0.15, [], True),
            BoundaryRelation.BODY,
            0.9,
        ),
        (
            BodyComponent("runtime_process", "process", {"execute"}, 1.0, {}, 0.05, [], False),
            BoundaryRelation.CORE_SELF,
            0.95,
        ),
    ]
    for component, relation, confidence in specs:
        body.register(component, SelfBoundary(component.component_id, relation, confidence))
    return body
