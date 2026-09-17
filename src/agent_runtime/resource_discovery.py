"""Evidence-first discovery of cheaper external resources.

The scout knows the *need* (for example, cheaper LLM inference), not a vendor
allowlist. Search results are untrusted opportunities until a bounded trial
produces evidence good enough for routing.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
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
class ResourceVerification:
    opportunity_id: str
    status: str
    relevance_score: float
    observed_at: str
    verification_url: str | None = None
    source_receipt_id: str = ""
    free_evidence: bool = False
    local_evidence: bool = False
    credential_evidence: bool = False
    privacy_caution: bool = False
    evidence_excerpt: str = ""
    evidence_refs: tuple[str, ...] = ()
    verification_version: str = "resource-verification.v3"


@dataclass(frozen=True)
class ProviderLead:
    lead_id: str
    source_opportunity_id: str
    provider_label: str
    credential_env: str
    signup_url: str
    official_root: str
    discovered_at: str
    source_receipt_id: str
    source_claim: str = ""
    claimed_free: bool = False
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

    def verify_pending(
        self,
        *,
        now: datetime,
        fetch: Callable[[str, int], Any],
        limit: int = 8,
    ) -> list[ResourceVerification]:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("resource verification requires timezone-aware time")
        current_versions = {row.opportunity_id: row.verification_version for row in self.verifications()}
        ranked = sorted(
            (item for item in self.opportunities()
             if item.status == "DISCOVERED" or current_versions.get(item.opportunity_id) != "resource-verification.v3"),
            key=lambda item: (-_relevance_score(item), item.discovered_at, item.opportunity_id),
        )
        results: list[ResourceVerification] = []
        for item in ranked[: max(1, min(int(limit), 25))]:
            score = _relevance_score(item)
            if score < 0.34:
                verification = ResourceVerification(
                    opportunity_id=item.opportunity_id,
                    status="REJECTED_IRRELEVANT",
                    relevance_score=score,
                    observed_at=_utc_iso(now),
                )
                self._write_verification(verification)
                self._write_opportunity(replace(item, status=verification.status))
                results.append(verification)
                continue
            receipt_id, page = _fetch_page(fetch(item.url, 24000))
            if not page:
                verification = ResourceVerification(
                    opportunity_id=item.opportunity_id,
                    status="VERIFICATION_UNAVAILABLE",
                    relevance_score=score,
                    observed_at=_utc_iso(now),
                    verification_url=item.url,
                    source_receipt_id=receipt_id,
                    evidence_refs=tuple(x for x in (receipt_id, "url:" + item.url) if x),
                )
            else:
                text = _text(page.get("markdown") or page.get("text") or page.get("content"), 24000)
                url = _canonical_public_url(page.get("url") or item.url) or item.url
                page_relevance = _page_relevance_score(item.resource_kind, text, url)
                free_evidence = _looks_free(text)
                local_evidence = _looks_local_capable(text)
                credential_evidence = _looks_like_credentials_needed(text)
                privacy_caution = _looks_like_privacy_caution(text)
                if page_relevance < 0.5:
                    status = "REJECTED_WRONG_RESOURCE"
                else:
                    status = "VERIFIED_CANDIDATE" if (free_evidence or local_evidence) else "VERIFIED_NO_SAVINGS_EVIDENCE"
                verification = ResourceVerification(
                    opportunity_id=item.opportunity_id,
                    status=status,
                    relevance_score=score,
                    observed_at=_utc_iso(now),
                    verification_url=url,
                    source_receipt_id=receipt_id,
                    free_evidence=free_evidence,
                    local_evidence=local_evidence,
                    credential_evidence=credential_evidence,
                    privacy_caution=privacy_caution,
                    evidence_excerpt=_evidence_excerpt(text),
                    evidence_refs=tuple(x for x in (receipt_id, "url:" + url) if x),
                )
            self._write_verification(verification)
            self._write_opportunity(replace(item, status=verification.status))
            results.append(verification)
        return results

    def verifications(self) -> list[ResourceVerification]:
        folder = self.root / "verifications"
        found: list[ResourceVerification] = []
        for path in sorted(folder.glob("*.json")) if folder.exists() else []:
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
                row["evidence_refs"] = tuple(row.get("evidence_refs") or ())
                if "verification_version" not in row:
                    row["verification_version"] = "resource-verification.v1"
                found.append(ResourceVerification(**row))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
        return found

    def expand_provider_leads(
        self,
        *,
        now: datetime,
        fetch: Callable[[str, int], Any],
        limit: int = 12,
    ) -> list[ProviderLead]:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("provider lead expansion requires timezone-aware time")
        opportunities = {item.opportunity_id: item for item in self.opportunities()}
        existing = {item.lead_id for item in self.provider_leads()}
        created: list[ProviderLead] = []
        candidates = [
            row for row in self.verifications()
            if row.status == "VERIFIED_CANDIDATE" and row.credential_evidence
        ]
        for verification in candidates[: max(1, min(int(limit), 25))]:
            opportunity = opportunities.get(verification.opportunity_id)
            source_url = verification.verification_url or (opportunity.url if opportunity else "")
            env_url = _github_env_example_url(source_url)
            if env_url is None:
                continue
            receipt_id, page = _fetch_page(fetch(env_url, 16000))
            if not page:
                continue
            raw = str(page.get("markdown") or page.get("text") or page.get("content") or "")
            for parsed in _parse_provider_env_example(raw):
                digest = sha256(
                    f"{verification.opportunity_id}\0{parsed['credential_env']}\0{parsed['signup_url']}".encode()
                ).hexdigest()[:24]
                lead_id = "provider-lead:" + digest
                if lead_id in existing:
                    continue
                lead = ProviderLead(
                    lead_id=lead_id,
                    source_opportunity_id=verification.opportunity_id,
                    provider_label=parsed["provider_label"],
                    credential_env=parsed["credential_env"],
                    signup_url=parsed["signup_url"],
                    official_root=_organization_root(parsed["signup_url"]),
                    discovered_at=_utc_iso(now),
                    source_receipt_id=receipt_id,
                    source_claim=parsed["source_claim"],
                    claimed_free=_looks_free(parsed["source_claim"]),
                    evidence_refs=tuple(x for x in (
                        verification.source_receipt_id, receipt_id, "url:" + env_url
                    ) if x),
                )
                self._write_provider_lead(lead)
                existing.add(lead_id)
                created.append(lead)
        return created

    def provider_leads(self) -> list[ProviderLead]:
        folder = self.root / "provider_leads"
        found: list[ProviderLead] = []
        for path in sorted(folder.glob("*.json")) if folder.exists() else []:
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
                row["evidence_refs"] = tuple(row.get("evidence_refs") or ())
                found.append(ProviderLead(**row))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
        return found

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

    def _write_verification(self, item: ResourceVerification) -> None:
        folder = self.root / "verifications"
        folder.mkdir(parents=True, exist_ok=True)
        key = item.opportunity_id.split(":")[-1]
        _atomic_json(folder / f"{key}.json", asdict(item))

    def _write_provider_lead(self, item: ProviderLead) -> None:
        folder = self.root / "provider_leads"
        folder.mkdir(parents=True, exist_ok=True)
        key = item.lead_id.split(":")[-1]
        _atomic_json(folder / f"{key}.json", asdict(item))

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
    return any(token in low for token in ("free tier", "free model", "free inference", "$0", "zero cost", "free api", "free, open source", "free open source"))


def _looks_like_credentials_needed(text: str) -> bool:
    low = text.lower()
    return any(token in low for token in ("api key", "sign up", "signup", "account", "credit card", "authentication"))


def _relevance_score(item: ResourceOpportunity) -> float:
    text = f"{item.title} {item.snippet} {item.url}".lower()
    weighted = {
        "llm": 0.28, "language model": 0.24, "inference": 0.22,
        "openai alternative": 0.32, "chat completion": 0.24,
        "hosted model": 0.20, "local model": 0.20, "transformer": 0.12,
        "api key": 0.08, "free tier": 0.18, "free api": 0.18,
    }
    score = sum(weight for token, weight in weighted.items() if token in text)
    if item.resource_kind == "llm_inference" and ("github.com" in text or "hub.docker.com" in text):
        score += 0.06
    return min(1.0, score)


def _github_env_example_url(value: str) -> str | None:
    url = _canonical_public_url(value)
    if url is None:
        return None
    parts = urlsplit(url)
    if (parts.hostname or "").lower() != "github.com":
        return None
    segments = [segment for segment in parts.path.split("/") if segment]
    if len(segments) < 2:
        return None
    owner, repo = segments[0], segments[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not owner or not repo:
        return None
    return f"https://raw.githubusercontent.com/{owner}/{repo}/HEAD/.env.example"


def _parse_provider_env_example(text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    comments: list[str] = []
    key_pattern = re.compile(r"^([A-Z][A-Z0-9_]{2,}(?:_API_KEY|_API_TOKEN|_TOKEN))\s*=")
    url_pattern = re.compile(r"https?://[^\s)>]+")
    for raw in str(text).splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            comment = line.lstrip("#").strip()
            if comment and set(comment) != {"─"}:
                comments.append(comment)
                comments = comments[-6:]
            continue
        match = key_pattern.match(line)
        if not match:
            comments = []
            continue
        credential_env = match.group(1)
        signup_url = ""
        for comment in reversed(comments):
            if "get key" in comment.lower() or "get token" in comment.lower() or "api key" in comment.lower():
                url_match = url_pattern.search(comment)
                if url_match:
                    signup_url = url_match.group(0).rstrip(".,;")
                    break
        signup_url = _canonical_public_url(signup_url) or ""
        if not signup_url:
            comments = []
            continue
        label = ""
        for comment in comments:
            low = comment.lower()
            if "get key" in low or "get token" in low or "api key" in low:
                continue
            candidate = re.split(r"\s+[—–-]\s+", comment, maxsplit=1)[0].strip()
            if candidate and len(candidate) <= 80:
                label = candidate
                break
        if not label:
            label = credential_env.rsplit("_", 2)[0].replace("_", " ").title()
        rows.append({
            "provider_label": label,
            "credential_env": credential_env,
            "signup_url": signup_url,
            "source_claim": " | ".join(comments)[:1200],
        })
        comments = []
    return rows


def _organization_root(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    parts = [part for part in host.split(".") if part]
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _page_relevance_score(resource_kind: str, text: str, url: str) -> float:
    low = f"{text} {url}".lower()
    if resource_kind != "llm_inference":
        return 0.5
    # Mentions like "no third-party LLM" describe a non-LLM system and must
    # not promote it. Remove explicit negations before looking for positive
    # task-interface evidence.
    positive = re.sub(r"\b(?:no|without|does not use|doesn't use)\s+(?:third[- ]party\s+)?llm\b", " ", low)
    if any(token in positive for token in (
        "openai compatible", "openai-compatible", "chat completion",
        "chat completions", "large language model", "language model api",
        "ollama", "vllm",
    )):
        return 1.0
    if re.search(r"\bllms?\b", positive) and any(token in positive for token in (
        "inference", "provider", "routing", "router", "api", "chat", "model",
    )):
        return 0.9
    if "language model" in positive and "inference" in positive:
        return 0.8
    return 0.0


def _fetch_page(value: Any) -> tuple[str, Mapping[str, Any] | None]:
    payload = getattr(value, "payload", value)
    receipt_id = str(getattr(value, "receipt_id", "") or "")
    if not isinstance(payload, Mapping) or not payload.get("ok"):
        return receipt_id, None
    page = payload.get("page")
    if not isinstance(page, Mapping) or not page.get("success"):
        return receipt_id, None
    return receipt_id, page


def _looks_local_capable(text: str) -> bool:
    low = text.lower()
    return any(token in low for token in (
        "run locally", "local inference", "self-hosted", "self hosted",
        "docker run", "open source openai alternative", "runs on cpu",
    ))


def _looks_like_privacy_caution(text: str) -> bool:
    low = text.lower()
    return any(token in low for token in (
        "used to train", "used for training", "improve our products",
        "may be used to improve", "data retention", "non-commercial use only",
    ))


def _evidence_excerpt(text: str) -> str:
    compact = " ".join(str(text).split())
    low = compact.lower()
    anchors = ("free tier", "free api", "free, open source", "run locally", "self-hosted", "api key", "no credit card")
    indexes = [low.find(token) for token in anchors if low.find(token) >= 0]
    start = max(0, (min(indexes) if indexes else 0) - 240)
    return compact[start:start + 1200]


def _utc_iso(now: datetime) -> str:
    return now.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)
