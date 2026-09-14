"""Bounded read-only observation providers owned by Agent Runtime."""
from .contract import ObservationFailure, ObservationResult
from .database_read import DatabaseReadPolicy, DatabaseSource, SQLAlchemyDatabaseRead
from .filesystem_events import FilesystemEventPolicy, WatchdogFilesystemEvents
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
    RuntimeObservationProvider as _RuntimeObservationProvider,
    register_observation_provider,
)


class RuntimeObservationProvider(_RuntimeObservationProvider):
    """Public provider facade that also owns extension resource lifecycles."""

    def close(self) -> None:
        if getattr(self, "_closed", False):
            return
        for extension in getattr(self, "_extensions", {}).values():
            close = getattr(extension, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
        super().close()


__all__ = [
    "BUILTIN_OBSERVATION_CAPABILITIES",
    "CLOCK_OBSERVE",
    "DATABASE_READ_OBSERVE",
    "DatabaseReadPolicy",
    "DatabaseSource",
    "ENVIRONMENT_OBSERVE",
    "FILESYSTEM_EVENTS_OBSERVE",
    "FilesystemEventPolicy",
    "GIT_OBSERVE",
    "HTTP_OBSERVE",
    "ObservationExtension",
    "ObservationFailure",
    "ObservationResult",
    "PROCESS_OBSERVE",
    "RuntimeObservationProvider",
    "SQLAlchemyDatabaseRead",
    "SYSTEM_OBSERVE",
    "WORKSPACE_OBSERVE",
    "WatchdogFilesystemEvents",
    "register_observation_provider",
]
