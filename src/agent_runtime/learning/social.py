"""Social learning must carry teacher, source, and provenance."""
from __future__ import annotations


class SocialLessonProvenanceError(ValueError):
    """Raised when a social lesson is missing teacher, source, or provenance."""
