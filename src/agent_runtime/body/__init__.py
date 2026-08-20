from .boundary import BoundaryRelation, SelfBoundary
from .component import BodyComponent
from .model import BodyModel
from .registry import digital_body, physical_body

__all__ = [
    "BodyComponent",
    "BodyModel",
    "BoundaryRelation",
    "SelfBoundary",
    "digital_body",
    "physical_body",
]
