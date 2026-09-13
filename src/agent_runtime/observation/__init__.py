"""Bounded read-only observation providers owned by Agent Runtime."""
from .contract import ObservationFailure, ObservationResult
from .provider import (
    BUILTIN_OBSERVATION_CAPABILITIES,
    CLOCK_OBSERVE,
    DATABASE_READ_OBSERVE,
    ENVIRONMENT_OBSERVE,
    FILESYSTEM_EVENTS_OBSERVE,
    GIT_OBSERVE,
    HTTP_OBSERVE,
    PROCESS_OBSERVE,
    SYSTEM_OBSERVE,
    WORKSPACE_OBSERVE,
    ObservationExtension,
    RuntimeObservationProvider,
    register_observation_provider,
)

__all__ = [
    "BUILTIN_OBSERVATION_CAPABILITIES",
    "CLOCK_OBSERVE",
    "DATABASE_READ_OBSERVE",
    "ENVIRONMENT_OBSERVE",
    "FILESYSTEM_EVENTS_OBSERVE",
    "GIT_OBSERVE",
    "HTTP_OBSERVE",
    "ObservationExtension",
    "ObservationFailure",
    "ObservationResult",
    "PROCESS_OBSERVE",
    "RuntimeObservationProvider",
    "SYSTEM_OBSERVE",
    "WORKSPACE_OBSERVE",
    "register_observation_provider",
]
