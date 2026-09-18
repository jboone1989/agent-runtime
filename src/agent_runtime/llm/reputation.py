"""Provider-neutral model reputation assembled from external leaderboards.

This module never hard-codes which model is "best".  It only knows how to:
- normalize model ids from different providers/leaderboards;
- combine current externally observed scores by task;
- rank a provider's currently available model ids.

Availability, price, quota and live endpoint health remain separate evidence.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import re
from typing import Any, Iterable, Mapping


_TASKS = {
    "general_text",
    "simple_public_text",
    "self_upgrade",
    "code_audit",
    "deep_research",
    "long_reflection",
    "architecture_review",
}


def _bounded(value: float) -> float:
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("reputation scores must be finite")
    return min(1.0, max(0.0, value))


def canonical_model_name(raw: str) -> str:
    """Normalize aliases enough to match the same model across providers.

    This is deliberately conservative: it removes provider namespaces, free
    suffixes and leaderboard release dates, but does not collapse major model
    variants such as Flash vs Pro or reasoning effort variants.
    """
    value = str(raw or "").strip().lower()
    if not value:
        return ""
    value = value.split(":", 1)[0]
    if "/" in value:
        value = value.rsplit("/", 1)[-1]
    value = value.replace("_", "-")
    value = re.sub(r"-20\d{6}$", "", value)
    value = re.sub(r"-\d{8}$", "", value)
    value = re.sub(r"-(?:free|preview)$", "", value)
    value = value.replace("5-3", "5.3").replace("2-6", "2.6")
    value = value.replace("3-5-lightning", "3.5-lightning")
    value = re.sub(r"-+", "-", value).strip("-")
    return value


@dataclass(frozen=True)
class ModelReputation:
    canonical_name: str
    overall: float = 0.5
    coding: float = 0.5
    agentic: float = 0.5
    human_preference: float = 0.5
    popularity: float = 0.5
    chinese: float = 0.5
    confidence: float = 0.5
    source_refs: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not str(self.canonical_name or "").strip():
            raise ValueError("canonical_name required")
        for name in (
            "overall", "coding", "agentic", "human_preference",
            "popularity", "chinese", "confidence",
        ):
            object.__setattr__(self, name, _bounded(getattr(self, name)))
        object.__setattr__(
            self,
            "source_refs",
            tuple(dict.fromkeys(str(x) for x in self.source_refs if str(x))),
        )

    def score_for(self, task_class: str) -> float:
        task = str(task_class or "general_text")
        if task == "code_audit":
            score = (
                self.coding * 0.48
                + self.agentic * 0.22
                + self.overall * 0.20
                + self.popularity * 0.10
            )
        elif task in {"self_upgrade", "architecture_review"}:
            score = (
                self.agentic * 0.36
                + self.overall * 0.34
                + self.coding * 0.20
                + self.human_preference * 0.10
            )
        elif task == "deep_research":
            score = (
                self.overall * 0.42
                + self.agentic * 0.28
                + self.human_preference * 0.20
                + self.popularity * 0.10
            )
        elif task == "long_reflection":
            score = (
                self.overall * 0.48
                + self.human_preference * 0.27
                + self.agentic * 0.15
                + self.popularity * 0.10
            )
        else:
            score = (
                self.overall * 0.42
                + self.human_preference * 0.28
                + self.popularity * 0.20
                + self.agentic * 0.10
            )
        # External scores are priors, not authority.  Low-confidence snapshots
        # shrink towards a neutral 0.5 rather than fabricating certainty.
        return _bounded(0.5 + (score - 0.5) * self.confidence)


class ModelReputationBook:
    def __init__(self, entries: Iterable[ModelReputation] = ()) -> None:
        self._entries = {
            canonical_model_name(item.canonical_name): item
            for item in entries
            if canonical_model_name(item.canonical_name)
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "ModelReputationBook":
        rows = payload.get("models") if isinstance(payload, Mapping) else None
        entries: list[ModelReputation] = []
        for raw in rows or ():
            if not isinstance(raw, Mapping):
                continue
            canonical = canonical_model_name(
                str(raw.get("canonical_name") or raw.get("model") or "")
            )
            if not canonical:
                continue
            entries.append(ModelReputation(
                canonical_name=canonical,
                overall=float(raw.get("overall", 0.5)),
                coding=float(raw.get("coding", 0.5)),
                agentic=float(raw.get("agentic", 0.5)),
                human_preference=float(raw.get("human_preference", 0.5)),
                popularity=float(raw.get("popularity", 0.5)),
                chinese=float(raw.get("chinese", 0.5)),
                confidence=float(raw.get("confidence", 0.5)),
                source_refs=tuple(raw.get("source_refs") or ()),
            ))
        return cls(entries)

    def get(self, model: str) -> ModelReputation | None:
        key = canonical_model_name(model)
        row = self._entries.get(key)
        if row is not None:
            return row

        # Leaderboards often append provider release tags while hosted APIs use
        # the family id.  Prefer the longest unambiguous family-prefix match.
        matches = [
            candidate
            for name, candidate in self._entries.items()
            if key.startswith(name + "-") or name.startswith(key + "-")
        ]
        if len(matches) == 1:
            return matches[0]
        return None

    def score(
        self,
        model: str,
        *,
        task_class: str = "general_text",
        unknown_score: float = 0.50,
    ) -> float:
        row = self.get(model)
        return (
            row.score_for(task_class)
            if row is not None
            else _bounded(unknown_score)
        )

    def rank(
        self,
        models: Iterable[str],
        *,
        task_class: str = "general_text",
        excluded: frozenset[str] = frozenset(),
    ) -> list[str]:
        unique = {
            str(model).strip()
            for model in models
            if str(model).strip() and str(model).strip() not in excluded
        }
        return sorted(
            unique,
            key=lambda model: (
                -self.score(model, task_class=task_class),
                canonical_model_name(model),
                model,
            ),
        )

    def ranker(self, *, task_class: str) -> Any:
        def _ranker(
            model_ids: list[str],
            excluded_models: frozenset[str],
        ) -> str | None:
            rows = self.rank(
                model_ids,
                task_class=task_class,
                excluded=excluded_models,
            )
            return rows[0] if rows else None

        return _ranker


__all__ = [
    "ModelReputation",
    "ModelReputationBook",
    "canonical_model_name",
]
