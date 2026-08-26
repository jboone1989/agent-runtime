"""Runtime execution gateway: authorize, run specialist backends, verify."""

from .gateway import RuntimeExecutionGateway, run_sync
from .policy import map_execution_policy
from .records import RuntimeExecutionResult
from .request import VerificationSpec
from .verification import (
    CommandObservation,
    LocalWorkspace,
    RuntimeVerifier,
    VerificationResult,
)

__all__ = [
    "CommandObservation",
    "LocalWorkspace",
    "RuntimeExecutionGateway",
    "RuntimeExecutionResult",
    "RuntimeVerifier",
    "VerificationResult",
    "VerificationSpec",
    "map_execution_policy",
    "run_sync",
]
