"""Evidence-first discovery of cheaper external resources.

The scout knows the *need* (for example, cheaper LLM inference), not a vendor
allowlist. Search results are untrusted opportunities until a bounded trial
produces evidence good enough for routing.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit, urlunsplit


@dataclass(frozen=True)
class ResourceNeed:
    resource_kind: str
    objective: str
    reason: str
    constraints: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResourceOpportunity:
    opportunity_id: str
    resource_kind: str
    title: str
    url: str
    snippet: str
    discovered_at: str
    search_query: str
    source_receipt_id: str
    claimed_free: bool
    credential_hint: bool
    source_class: str = "general"
    evidence_refs: tuple[str, ...] = ()
    status: str = "DISCOVERED"


@dataclass(frozen=True)
class ModelTrial:
    trial_id: str
    opportunity_id: str
    task_class: str
    status: str
    quality_score: float | None = None
    latency_ms: float | None = None
    observed_cost: float | None = None
    evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProviderProfile:
    opportunity_id: str
    task_class: str
    viable: bool
    mean_quality: float
    mean_latency_ms: float
    mean_cost: float
    trial_count: int
    evidence_refs: tuple[str, ...] = ()


class ResourceScout:
    """Search public evidence and persist vendor-neutral resource opportunities."""

    def __init__(self, state_dir: Path, search: Callable[[str, int, str], Any]) -> None:
        self.root = Path(state_dir) / "resource_discovery"
        self.search = search

    def discover(
        self,
        need: ResourceNeed,
        *,
        now: datetime,
        max_queries: int = 6,
        results_per_query: int = 6,
    ) -> list[ResourceOpportunity]:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("resource discovery requires timezone-aware time")
        plans = self._search_plans(need)[: max(1, min(max_queries, 8))]
        existing = {item.url: item for item in self.opportunities()}
        created: list[ResourceOpportunity] = []
        attempts: list[dict[str, Any]] = []
        for query, source_class in plans:
            receipt_id, rows = _search_rows(self.search(query, results_per_query, source_class))
            attempts.append({
                "query": query,
                "source_class": source_class,
                "result_count": len(rows),
                "source_receipt_id": receipt_id,
            })
            for row in rows:
                url = _canonical_public_url(row.get("url"))
                if url is None or url in existing:
                    continue
                title = _text(row.get("title"), 300)
                snippet = _text(row.get("snippet") or row.get("content"), 2000)
                digest = sha256(f"{need.resource_kind}\0{url}".encode()).hexdigest()[:24]
                opportunity = ResourceOpportunity(
                    opportunity_id="resource-opportunity:" + digest,
                    resource_kind=need.resource_kind,
                    title=title,
                    url=url,
                    snippet=snippet,
                    discovered_at=now.astimezone(timezone.utc).replace(microsecond=0).isoformat(),
                    search_query=query,
                    source_receipt_id=receipt_id,
                    claimed_free=_looks_free(title + " " + snippet),
                    credential_hint=_looks_like_credentials_needed(title + " " + snippet),
                    source_class=source_class,
                    evidence_refs=tuple(x for x in (receipt_id, "url:" + url) if x),
                )
                self._write_opportunity(opportunity)
                existing[url] = opportunity
                created.append(opportunity)
        self._write_run(need, now, attempts, created)
        return created

    def opportunities(self) -> list[ResourceOpportunity]:
        folder = self.root / "opportunities"
        found: list[ResourceOpportunity] = []
        for path in sorted(folder.glob("*.json")) if folder.exists() else []:
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
                row["evidence_refs"] = tuple(row.get("evidence_refs") or ())
                found.append(ResourceOpportunity(**row))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
        return found

    @staticmethod
    def _search_plans(need: ResourceNeed) -> list[tuple[str, str]]:
        kind = " ".join(need.resource_kind.replace("_", " ").split())
        objective = " ".join(need.objective.split())
        # Broad web search can fail because of rate limits/CAPTCHAs. The fallback
        # changes evidence ecosystems, not vendors: technical/open-source indexes
        # are searched with compact queries derived from the same resource need.
        return [
            (f"{kind} {objective} free tier API current pricing rate limits".strip(), "general"),
            (f"free {kind} API current".strip(), "general"),
            (f"free {kind} API".strip(), "technical"),
            (f"open source {kind} inference API".strip(), "technical"),
            (f"local {kind} small model CPU".strip(), "technical"),
            (f"{kind} hosted free inference".strip(), "technical"),
        ]

    def _write_opportunity(self, item: ResourceOpportunity) -> None:
        folder = self.root / "opportunities"
        folder.mkdir(parents=True, exist_ok=True)
        _atomic_json(folder / f"{item.opportunity_id.split(':')[-1]}.json", asdict(item))

    def _write_run(self, need: ResourceNeed, now: datetime, attempts: list[dict[str, Any]], created: list[ResourceOpportunity]) -> None:
        folder = self.root / "runs"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        payload = {
            "schema_version": "runtime-resource-discovery.v2",
            "observed_at": now.astimezone(timezone.utc).replace(microsecond=0).isoformat(),
            "need": asdict(need),
            "queries": [item["query"] for item in attempts],
            "search_attempts": attempts,
            "new_opportunity_ids": [item.opportunity_id for item in created],
        }
        _atomic_json(folder / f"{stamp}.json", payload)


def provider_profiles(trials: list[ModelTrial], *, minimum_quality: float = 0.75) -> list[ProviderProfile]:
    grouped: dict[tuple[str, str], list[ModelTrial]] = {}
    for trial in trials:
        if trial.status != "COMPLETED" or trial.quality_score is None or trial.latency_ms is None or trial.observed_cost is None:
            continue
        grouped.setdefault((trial.opportunity_id, trial.task_class), []).append(trial)
    out: list[ProviderProfile] = []
    for (opportunity_id, task_class), rows in sorted(grouped.items()):
        q = sum(float(x.quality_score) for x in rows) / len(rows)
        latency = sum(float(x.latency_ms) for x in rows) / len(rows)
        cost = sum(float(x.observed_cost) for x in rows) / len(rows)
        refs = tuple(dict.fromkeys(ref for x in rows for ref in x.evidence_refs))
        out.append(ProviderProfile(opportunity_id, task_class, q >= minimum_quality, q, latency, cost, len(rows), refs))
    return out


def select_provider(profiles: list[ProviderProfile], *, task_class: str, quality_floor: float) -> ProviderProfile | None:
    """Choose only empirically viable profiles; never route from marketing claims."""
    candidates = [x for x in profiles if x.task_class == task_class and x.viable and x.mean_quality >= quality_floor]
    if not candidates:
        return None
    return min(candidates, key=lambda x: (x.mean_cost, x.mean_latency_ms, -x.mean_quality, x.opportunity_id))


def _search_rows(value: Any) -> tuple[str, list[Mapping[str, Any]]]:
    payload = getattr(value, "payload", value)
    receipt_id = str(getattr(value, "receipt_id", "") or "")
    if not isinstance(payload, Mapping):
        return receipt_id, []
    rows = payload.get("results") or []
    return receipt_id, [x for x in rows if isinstance(x, Mapping)] if isinstance(rows, list) else []


def _canonical_public_url(value: Any) -> str | None:
    if not isinstance(value, str): return None
    try: parts = urlsplit(value.strip())
    except ValueError: return None
    if parts.scheme not in {"http", "https"} or not parts.hostname: return None
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", parts.query, ""))


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _looks_free(text: str) -> bool:
    low = text.lower()
    return any(token in low for token in ("free tier", "free model", "free inference", "$0", "zero cost", "free api"))


def _looks_like_credentials_needed(text: str) -> bool:
    low = text.lower()
    return any(token in low for token in ("api key", "sign up", "signup", "account", "credit card", "authentication"))


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)
