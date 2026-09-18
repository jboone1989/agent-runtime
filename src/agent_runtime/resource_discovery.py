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
    search_constraints: tuple[str, ...] = ()


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


PROVIDER_CATALOGUE_PARSER_VERSION = "provider-catalogue-parser.v2"


@dataclass(frozen=True)
class ProviderCatalogueExpansion:
    opportunity_id: str
    source_url: str
    parser_version: str
    expanded_at: str
    status: str
    lead_ids: tuple[str, ...] = ()


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
class ProviderVerification:
    lead_id: str
    status: str
    observed_at: str
    free_status: str = "UNKNOWN"
    payment_method_required: bool | None = None
    privacy_caution: bool = False
    rate_limit_evidence: bool = False
    official_urls: tuple[str, ...] = ()
    source_receipt_ids: tuple[str, ...] = ()
    evidence_excerpt: str = ""
    evidence_refs: tuple[str, ...] = ()
    verification_version: str = "provider-verification.v2"


@dataclass(frozen=True)
class ProviderTrialPlan:
    plan_id: str
    lead_id: str
    provider_label: str
    credential_env: str
    status: str
    priority_score: float
    task_class: str = "simple_public_text"
    data_policy: str = "PUBLIC_SYNTHETIC_ONLY"
    max_requests: int = 3
    reasons: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    policy_version: str = "provider-trial-bootstrap.v2"


@dataclass(frozen=True)
class ResourceAcquisitionOutcome:
    outcome_id: str
    plan_id: str
    lead_id: str
    status: str
    observed_at: str
    friction_kind: str = "NONE"
    source: str = "RUNTIME"
    detail_summary: str = ""
    evidence_refs: tuple[str, ...] = ()
    schema_version: str = "resource-acquisition-outcome.v1"

    def __post_init__(self) -> None:
        statuses = {
            "BLOCKED_EXTERNAL", "CREDENTIAL_ACQUIRED", "TRIAL_STARTED",
            "TRIAL_FAILED", "TRIAL_SUCCEEDED", "NO_VIABLE_MODEL", "ABANDONED",
            "MODEL_TARGETS_INCOMPATIBLE", "FREE_CANDIDATES_EXHAUSTED",
            "TEMPORARY_RATE_LIMIT", "TASK_MISMATCH", "AUTHENTICATION_REJECTED",
            "PROVIDER_UNAVAILABLE",
        }
        frictions = {
            "NONE", "PHONE_VERIFICATION", "PAYMENT_METHOD", "REGION_RESTRICTION",
            "ACCOUNT_REQUIRED", "MANUAL_APPROVAL", "UNKNOWN_EXTERNAL",
        }
        if self.status not in statuses:
            raise ValueError(f"unsupported acquisition outcome status: {self.status}")
        if self.friction_kind not in frictions:
            raise ValueError(f"unsupported acquisition friction: {self.friction_kind}")
        if self.status == "BLOCKED_EXTERNAL" and self.friction_kind == "NONE":
            raise ValueError("blocked acquisition outcome requires friction_kind")


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
    provider_lead_id: str = ""
    provider_label: str = ""
    model_id: str = ""
    started_at: str = ""
    completed_at: str = ""
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    error_code: str = ""
    trial_version: str = "provider-model-trial.v1"


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
    model_id: str = ""


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
            try:
                fetched = fetch(item.url, 24000)
            except Exception:  # External evidence failure must not abort the batch.
                fetched = {"ok": False}
            receipt_id, page = _fetch_page(fetched)
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

    def provider_catalogue_expansions(self) -> list[ProviderCatalogueExpansion]:
        folder = self.root / "provider_catalogue_expansions"
        found: list[ProviderCatalogueExpansion] = []
        for path in sorted(folder.glob("*.json")) if folder.exists() else []:
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
                row["lead_ids"] = tuple(row.get("lead_ids") or ())
                found.append(ProviderCatalogueExpansion(**row))
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
        existing = {item.lead_id: item for item in self.provider_leads()}
        expansions = {item.opportunity_id: item for item in self.provider_catalogue_expansions()}
        historical_lead_ids: dict[str, list[str]] = {}
        lead_folder = self.root / "provider_leads"
        for path in sorted(lead_folder.glob("*.json")) if lead_folder.exists() else []:
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
                opportunity_id = str(row.get("source_opportunity_id") or "")
                lead_id = str(row.get("lead_id") or "")
                if opportunity_id and lead_id:
                    historical_lead_ids.setdefault(opportunity_id, []).append(lead_id)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
        created: list[ProviderLead] = []

        def safe_fetch(url: str, max_chars: int) -> tuple[str, Mapping[str, Any] | None]:
            try:
                return _fetch_page(fetch(url, max_chars))
            except Exception:  # A broken catalogue endpoint must not abort expansion.
                return "", None

        candidates = [
            row for row in self.verifications()
            if row.status == "VERIFIED_CANDIDATE" and row.credential_evidence
        ]
        for verification in candidates[: max(1, min(int(limit), 25))]:
            opportunity = opportunities.get(verification.opportunity_id)
            source_url = verification.verification_url or (opportunity.url if opportunity else "")
            marker = expansions.get(verification.opportunity_id)
            if (
                marker is not None
                and marker.parser_version == PROVIDER_CATALOGUE_PARSER_VERSION
                and marker.source_url == source_url
            ):
                continue
            if marker is None:
                migrated_ids = tuple(dict.fromkeys(historical_lead_ids.get(verification.opportunity_id) or ()))
                if migrated_ids:
                    migrated = ProviderCatalogueExpansion(
                        opportunity_id=verification.opportunity_id,
                        source_url=source_url,
                        parser_version=PROVIDER_CATALOGUE_PARSER_VERSION,
                        expanded_at=_utc_iso(now),
                        status="MIGRATED",
                        lead_ids=migrated_ids,
                    )
                    self._write_provider_catalogue_expansion(migrated)
                    expansions[verification.opportunity_id] = migrated
                    continue

            parsed_sources: list[tuple[dict[str, str], str, str]] = []
            fetch_succeeded = False

            # The original high-precision path remains preferred: public GitHub
            # catalogues often document provider credentials in .env.example.
            env_url = _github_env_example_url(source_url)
            if env_url is not None:
                receipt_id, page = safe_fetch(env_url, 16000)
                if page:
                    fetch_succeeded = True
                    raw = str(page.get("markdown") or page.get("text") or page.get("content") or "")
                    parsed_sources.extend(
                        (parsed, receipt_id, env_url)
                        for parsed in _parse_provider_env_example(raw)
                    )

            # A verified catalogue may instead expose provider links in its README
            # or comparison table. Those links are discovery clues only. They are
            # still forced through verify_provider_leads(), which accepts evidence
            # solely from each candidate's official domain before any trial plan.
            if source_url:
                receipt_id, page = safe_fetch(source_url, 24000)
                if page:
                    fetch_succeeded = True
                    raw = str(page.get("markdown") or page.get("text") or page.get("content") or "")
                    parsed_sources.extend(
                        (parsed, receipt_id, source_url)
                        for parsed in _parse_provider_catalogue(raw, source_url=source_url)
                    )

            seen_parsed: set[tuple[str, str]] = set()
            expanded_lead_ids: list[str] = []
            for parsed, receipt_id, evidence_url in parsed_sources:
                parsed_key = (parsed["credential_env"], parsed["signup_url"])
                if parsed_key in seen_parsed:
                    continue
                seen_parsed.add(parsed_key)
                digest = sha256(
                    f"{verification.opportunity_id}\0{parsed['credential_env']}\0{parsed['signup_url']}".encode()
                ).hexdigest()[:24]
                lead_id = "provider-lead:" + digest
                prior = existing.get(lead_id)
                expanded_lead_ids.append(lead_id)
                lead = ProviderLead(
                    lead_id=lead_id,
                    source_opportunity_id=verification.opportunity_id,
                    provider_label=parsed["provider_label"],
                    credential_env=parsed["credential_env"],
                    signup_url=parsed["signup_url"],
                    official_root=_organization_root(parsed["signup_url"]),
                    discovered_at=prior.discovered_at if prior else _utc_iso(now),
                    source_receipt_id=receipt_id,
                    source_claim=parsed["source_claim"],
                    claimed_free=_looks_free(parsed["source_claim"]),
                    evidence_refs=tuple(dict.fromkeys(x for x in (
                        *((prior.evidence_refs if prior else ())),
                        verification.source_receipt_id, receipt_id, "url:" + evidence_url
                    ) if x)),
                    status=prior.status if prior else "DISCOVERED",
                )
                if prior == lead:
                    continue
                self._write_provider_lead(lead)
                existing[lead_id] = lead
                created.append(lead)

            if fetch_succeeded:
                expanded = ProviderCatalogueExpansion(
                    opportunity_id=verification.opportunity_id,
                    source_url=source_url,
                    parser_version=PROVIDER_CATALOGUE_PARSER_VERSION,
                    expanded_at=_utc_iso(now),
                    status="EXPANDED" if expanded_lead_ids else "EXPANDED_NO_LEADS",
                    lead_ids=tuple(dict.fromkeys(expanded_lead_ids)),
                )
                self._write_provider_catalogue_expansion(expanded)
                expansions[verification.opportunity_id] = expanded
        return created

    def provider_leads(self) -> list[ProviderLead]:
        """Return the active provider view while retaining all raw lead files.

        Catalogue extraction is append-only for auditability. Historical noise
        stays on disk but is filtered from active verification/planning. A
        catalogue's own domain cannot become a provider, and duplicate records
        collapse first by organization root and then by credential identity.
        """
        folder = self.root / "provider_leads"
        opportunity_roots = {
            item.opportunity_id: _organization_root(item.url)
            for item in self.opportunities()
        }
        found: list[ProviderLead] = []
        for path in sorted(folder.glob("*.json")) if folder.exists() else []:
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
                row["evidence_refs"] = tuple(row.get("evidence_refs") or ())
                lead = ProviderLead(**row)
                label = _provider_label_candidate(lead.provider_label)
                root = lead.official_root.lower().rstrip(".")
                if label is None or not root:
                    continue
                if root == opportunity_roots.get(lead.source_opportunity_id):
                    continue
                claim_low = lead.source_claim.lower()
                label_low = lead.provider_label.lower()
                catalogue_markers = (
                    "compare providers", "submit a provider", "directory at",
                    "directory of", "community-maintained reference",
                    "each provider live", "provider/<slug>",
                )
                if root in label_low and any(marker in claim_low for marker in catalogue_markers):
                    continue
                found.append(lead)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
        rank = {
            "VERIFIED_RECURRING_FREE": 0,
            "VERIFIED_TRIAL_CREDIT": 1,
            "OFFICIAL_EVIDENCE_INCONCLUSIVE": 2,
            "OFFICIAL_EVIDENCE_UNAVAILABLE": 3,
            "VERIFIED_PAID_ONLY": 4,
            "DISCOVERED": 5,
        }

        def quality(lead: ProviderLead) -> tuple[int, str, str]:
            return (rank.get(lead.status, 6), lead.discovered_at, lead.lead_id)

        by_root: dict[str, ProviderLead] = {}
        for lead in found:
            root = lead.official_root.lower().rstrip(".")
            prior = by_root.get(root)
            if prior is None or quality(lead) < quality(prior):
                by_root[root] = lead

        by_credential: dict[str, ProviderLead] = {}
        for lead in by_root.values():
            credential = lead.credential_env.upper().strip()
            prior = by_credential.get(credential)
            if prior is None or quality(lead) < quality(prior):
                by_credential[credential] = lead
        return sorted(by_credential.values(), key=lambda item: (item.discovered_at, item.lead_id))

    def verify_provider_leads(
        self,
        *,
        now: datetime,
        fetch: Callable[[str, int], Any],
        limit: int = 8,
        results_per_query: int = 8,
    ) -> list[ProviderVerification]:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("provider verification requires timezone-aware time")
        current = {row.lead_id: row.verification_version for row in self.provider_verifications()}
        leads = [row for row in self.provider_leads() if current.get(row.lead_id) != "provider-verification.v2"]
        results: list[ProviderVerification] = []
        for lead in leads[: max(1, min(int(limit), 25))]:
            queries = (
                f"{lead.provider_label} API pricing",
                f"{lead.provider_label} free tier rate limits API",
                f"{lead.provider_label} API documentation models pricing",
            )
            official: list[str] = []
            receipt_ids: list[str] = []
            failure_refs: list[str] = []
            for query in queries:
                try:
                    searched = self.search(
                        query.strip(), max(1, min(int(results_per_query), 20)), "general"
                    )
                except Exception as exc:  # External search failure must not abort the batch.
                    failure_refs.append(f"search-error:{type(exc).__name__}")
                    continue
                search_receipt, rows = _search_rows(searched)
                if search_receipt:
                    receipt_ids.append(search_receipt)
                for row in rows:
                    url = _canonical_public_url(row.get("url"))
                    if not url or not _matches_official_root(url, lead.official_root):
                        continue
                    context = " ".join(str(row.get(k) or "") for k in ("title", "snippet", "content"))
                    if not _provider_search_result_relevant(lead.provider_label, context + " " + url):
                        continue
                    if url not in official:
                        official.append(url)
                if len(official) >= 3:
                    break
            candidates = list(dict.fromkeys([lead.signup_url, *official]))[:5]
            fetched_urls: list[str] = []
            evidence_chunks: list[str] = []
            for url in candidates:
                try:
                    fetched = fetch(url, 28000)
                except Exception as exc:  # One blocked page must not abort other providers.
                    failure_refs.append(f"fetch-error:{type(exc).__name__}:url:{url}")
                    continue
                receipt_id, page = _fetch_page(fetched)
                if receipt_id:
                    receipt_ids.append(receipt_id)
                if not page:
                    continue
                final_url = _canonical_public_url(page.get("url") or url) or url
                if not _matches_official_root(final_url, lead.official_root):
                    continue
                text = _text(page.get("markdown") or page.get("text") or page.get("content"), 28000)
                if _provider_page_relevance(lead.provider_label, text + " " + final_url) < 0.35:
                    continue
                fetched_urls.append(final_url)
                evidence_chunks.append(text)
            combined = " ".join(evidence_chunks)
            free_status = _classify_free_status(combined) if combined else "UNKNOWN"
            payment_required = _payment_method_required(combined) if combined else None
            if not fetched_urls:
                status = "OFFICIAL_EVIDENCE_UNAVAILABLE"
            elif free_status == "RECURRING_FREE":
                status = "VERIFIED_RECURRING_FREE"
            elif free_status == "TRIAL_CREDIT":
                status = "VERIFIED_TRIAL_CREDIT"
            elif free_status == "PAID_ONLY":
                status = "VERIFIED_PAID_ONLY"
            else:
                status = "OFFICIAL_EVIDENCE_INCONCLUSIVE"
            verification = ProviderVerification(
                lead_id=lead.lead_id,
                status=status,
                observed_at=_utc_iso(now),
                free_status=free_status,
                payment_method_required=payment_required,
                privacy_caution=_looks_like_privacy_caution(combined),
                rate_limit_evidence=_looks_like_rate_limits(combined),
                official_urls=tuple(dict.fromkeys(fetched_urls)),
                source_receipt_ids=tuple(dict.fromkeys(x for x in receipt_ids if x)),
                evidence_excerpt=_provider_evidence_excerpt(combined),
                evidence_refs=tuple(dict.fromkeys([
                    *lead.evidence_refs,
                    *(f"url:{url}" for url in fetched_urls),
                    *(x for x in receipt_ids if x),
                    *failure_refs,
                ])),
            )
            self._write_provider_verification(verification)
            self._write_provider_lead(replace(lead, status=status))
            results.append(verification)
        return results

    def provider_verifications(self) -> list[ProviderVerification]:
        folder = self.root / "provider_verifications"
        found: list[ProviderVerification] = []
        for path in sorted(folder.glob("*.json")) if folder.exists() else []:
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
                row["official_urls"] = tuple(row.get("official_urls") or ())
                row["source_receipt_ids"] = tuple(row.get("source_receipt_ids") or ())
                row["evidence_refs"] = tuple(row.get("evidence_refs") or ())
                if "verification_version" not in row:
                    row["verification_version"] = "provider-verification.legacy"
                found.append(ProviderVerification(**row))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
        return found

    def record_model_trial(self, item: ModelTrial) -> None:
        if not item.trial_id or not item.opportunity_id or not item.task_class:
            raise ValueError("model trial identity fields must be non-empty")
        folder = self.root / "model_trials"
        folder.mkdir(parents=True, exist_ok=True)
        key = sha256(item.trial_id.encode()).hexdigest()[:24]
        _atomic_json(folder / f"{key}.json", asdict(item))

    def model_trials(self) -> list[ModelTrial]:
        folder = self.root / "model_trials"
        found: list[ModelTrial] = []
        for path in sorted(folder.glob("*.json")) if folder.exists() else []:
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
                row["evidence_refs"] = tuple(row.get("evidence_refs") or ())
                if "trial_version" not in row:
                    row["trial_version"] = "provider-model-trial.legacy"
                found.append(ModelTrial(**row))
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

    def pending_verification_count(self) -> int:
        current_versions = {row.opportunity_id: row.verification_version for row in self.verifications()}
        return sum(
            1 for item in self.opportunities()
            if item.status == "DISCOVERED" or current_versions.get(item.opportunity_id) != "resource-verification.v3"
        )

    def pending_provider_verification_count(self) -> int:
        current_versions = {row.lead_id: row.verification_version for row in self.provider_verifications()}
        return sum(
            1 for item in self.provider_leads()
            if current_versions.get(item.lead_id) != "provider-verification.v2"
        )

    @staticmethod
    def _search_plans(need: ResourceNeed) -> list[tuple[str, str]]:
        kind = " ".join(need.resource_kind.replace("_", " ").split())
        objective = " ".join(need.objective.split())
        adaptive = " ".join(" ".join(str(x).split()) for x in need.search_constraints if x)[:240]
        # Broad web search can fail because of rate limits/CAPTCHAs. The fallback
        # changes evidence ecosystems, not vendors. Observed acquisition friction
        # may narrow the search toward lower-friction routes without naming a vendor.
        return [
            (f"{kind} {objective} free tier API current pricing rate limits {adaptive}".strip(), "general"),
            (f"free {kind} API current {adaptive}".strip(), "general"),
            (f"free {kind} API {adaptive}".strip(), "technical"),
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

    def _write_provider_catalogue_expansion(self, item: ProviderCatalogueExpansion) -> None:
        folder = self.root / "provider_catalogue_expansions"
        folder.mkdir(parents=True, exist_ok=True)
        key = item.opportunity_id.split(":")[-1]
        _atomic_json(folder / f"{key}.json", asdict(item))

    def _write_provider_verification(self, item: ProviderVerification) -> None:
        folder = self.root / "provider_verifications"
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


def provider_profiles(
    trials: list[ModelTrial], *, minimum_quality: float = 0.75, minimum_trials: int = 1,
) -> list[ProviderProfile]:
    grouped: dict[tuple[str, str, str], list[ModelTrial]] = {}
    for trial in trials:
        if trial.status != "COMPLETED" or trial.quality_score is None or trial.latency_ms is None or trial.observed_cost is None:
            continue
        grouped.setdefault((trial.opportunity_id, trial.task_class, trial.model_id or ""), []).append(trial)
    out: list[ProviderProfile] = []
    for (opportunity_id, task_class, model_id), rows in sorted(grouped.items()):
        q = sum(float(x.quality_score) for x in rows) / len(rows)
        latency = sum(float(x.latency_ms) for x in rows) / len(rows)
        cost = sum(float(x.observed_cost) for x in rows) / len(rows)
        refs = tuple(dict.fromkeys(ref for x in rows for ref in x.evidence_refs))
        viable = len(rows) >= max(1, int(minimum_trials)) and q >= minimum_quality
        out.append(ProviderProfile(
            opportunity_id=opportunity_id, task_class=task_class, viable=viable,
            mean_quality=q, mean_latency_ms=latency, mean_cost=cost,
            trial_count=len(rows), evidence_refs=refs, model_id=model_id,
        ))
    return out


def select_provider(profiles: list[ProviderProfile], *, task_class: str, quality_floor: float) -> ProviderProfile | None:
    """Choose only empirically viable profiles; never route from marketing claims."""
    candidates = [x for x in profiles if x.task_class == task_class and x.viable and x.mean_quality >= quality_floor]
    if not candidates:
        return None
    return min(candidates, key=lambda x: (x.mean_cost, x.mean_latency_ms, -x.mean_quality, x.opportunity_id, x.model_id))


def resource_discovery_constraints(
    outcomes: list[ResourceAcquisitionOutcome],
) -> tuple[str, ...]:
    """Translate observed acquisition friction into vendor-neutral search constraints."""
    mapping = {
        "PHONE_VERIFICATION": "without phone verification",
        "PAYMENT_METHOD": "no credit card required",
        "REGION_RESTRICTION": "broad regional availability",
        "ACCOUNT_REQUIRED": "no signup required if possible",
        "MANUAL_APPROVAL": "instant self-service API access",
        "UNKNOWN_EXTERNAL": "low signup friction",
    }
    values: list[str] = []
    for row in outcomes:
        if row.status != "BLOCKED_EXTERNAL":
            continue
        value = mapping.get(row.friction_kind)
        if value and value not in values:
            values.append(value)
    return tuple(values)


def plan_provider_trials(
    leads: list[ProviderLead],
    verifications: list[ProviderVerification],
    *,
    available_credentials: set[str] | frozenset[str] = frozenset(),
    acquisition_outcomes: list[ResourceAcquisitionOutcome] | tuple[ResourceAcquisitionOutcome, ...] = (),
    task_class: str = "simple_public_text",
    limit: int = 3,
) -> list[ProviderTrialPlan]:
    """Rank bounded provider trials from evidence and observed acquisition outcomes.

    Vendor identity is never scored. External-world friction is feedback: a blocked
    path is deprioritized until its credential becomes available, while other verified
    paths can advance without a hard-coded provider fallback.
    """
    verified = {row.lead_id: row for row in verifications}
    latest: dict[str, ResourceAcquisitionOutcome] = {}
    for outcome in acquisition_outcomes:
        prior = latest.get(outcome.lead_id)
        if prior is None or outcome.observed_at >= prior.observed_at:
            latest[outcome.lead_id] = outcome
    plans: list[ProviderTrialPlan] = []
    for lead in leads:
        row = verified.get(lead.lead_id)
        if row is None or row.status not in {"VERIFIED_RECURRING_FREE", "VERIFIED_TRIAL_CREDIT"}:
            continue
        score = 0.0
        reasons: list[str] = []
        if row.free_status == "RECURRING_FREE":
            score += 0.55; reasons.append("recurring_free_evidence")
        elif row.free_status == "TRIAL_CREDIT":
            score += 0.22; reasons.append("trial_credit_only")
        if row.payment_method_required is False:
            score += 0.12; reasons.append("no_payment_method_evidence")
        elif row.payment_method_required is True:
            score -= 0.25; reasons.append("payment_method_required")
        if row.rate_limit_evidence:
            score += 0.08; reasons.append("rate_limits_observed")
        if row.privacy_caution:
            score -= 0.20; reasons.append("privacy_caution")
        ready = lead.credential_env in available_credentials
        outcome = latest.get(lead.lead_id)
        blocked = bool(outcome and outcome.status == "BLOCKED_EXTERNAL" and not ready)
        model_replan = bool(
            outcome and outcome.status in {
                "NO_VIABLE_MODEL",  # legacy: old code over-generalized model evidence
                "MODEL_TARGETS_INCOMPATIBLE", "FREE_CANDIDATES_EXHAUSTED",
                "TEMPORARY_RATE_LIMIT", "TASK_MISMATCH",
            }
        )
        provider_unavailable = bool(outcome and outcome.status == "PROVIDER_UNAVAILABLE")
        auth_rejected = bool(outcome and outcome.status == "AUTHENTICATION_REJECTED")
        if ready:
            score += 0.05; reasons.append("credential_available")
        else:
            reasons.append("credential_missing")
        if blocked and outcome is not None:
            score -= 0.70
            reasons.append("external_acquisition_friction")
            reasons.append("friction:" + outcome.friction_kind.lower())
        if model_replan:
            score -= 0.05
            reasons.append("model_level_replan_observed")
        if auth_rejected:
            score -= 0.30
            reasons.append("credential_rejected_observed")
        if provider_unavailable:
            score -= 1.0
            reasons.append("provider_unavailable_observed")
        if provider_unavailable:
            status = "PROVIDER_UNAVAILABLE"
        elif auth_rejected:
            status = "NEEDS_CREDENTIAL"
        elif ready:
            status = "READY_TO_TRIAL"
        elif blocked:
            status = "BLOCKED_BY_FRICTION"
        else:
            status = "NEEDS_CREDENTIAL"
        digest = sha256(f"{lead.lead_id}\0{task_class}\0provider-trial-bootstrap.v2".encode()).hexdigest()[:24]
        evidence = [*lead.evidence_refs, *row.evidence_refs]
        if outcome is not None:
            evidence.extend(outcome.evidence_refs)
        plans.append(ProviderTrialPlan(
            plan_id="provider-trial-plan:" + digest,
            lead_id=lead.lead_id,
            provider_label=lead.provider_label,
            credential_env=lead.credential_env,
            status=status,
            priority_score=round(score, 6),
            task_class=task_class,
            reasons=tuple(reasons),
            evidence_refs=tuple(dict.fromkeys(evidence)),
        ))
    return sorted(
        plans,
        key=lambda item: (
            item.status in {"BLOCKED_BY_FRICTION", "PROVIDER_UNAVAILABLE"},
            -item.priority_score,
            item.lead_id,
        ),
    )[: max(1, min(int(limit), 10))]


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
            comments = []
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


def _derived_provider_credential_env(provider_label: str) -> str:
    """Return our local secret name for a discovered provider lead.

    This is a LivingRuntime storage convention, not a claim about the
    provider's documented environment-variable name.
    """
    stem = re.sub(r"[^A-Z0-9]+", "_", str(provider_label).upper()).strip("_")
    stem = re.sub(r"_(?:API|AI)$", "", stem).strip("_")
    if not stem:
        stem = "MODEL_PROVIDER"
    return (stem[:48].rstrip("_") or "MODEL_PROVIDER") + "_API_KEY"


def _provider_label_candidate(value: str) -> str | None:
    text = re.sub(r"[*_`#]+", " ", str(value))
    text = re.sub(r"\s+", " ", text).strip(" -–—:|[]()")
    if not text or len(text) > 64 or text.startswith("@"):
        return None
    low = text.lower().rstrip(".")
    generic = {
        "api", "api key", "key", "token", "docs", "documentation", "website",
        "pricing", "models", "model", "console", "dashboard", "sign up",
        "signup", "get key", "get api key", "free tier", "here", "link",
        "ai studio", "models page", "pricing page", "available regions", "terms",
        "quickstart", "subscriptions", "data usage", "service tiers", "rate limits",
        "limits documentation", "authentication faq", "registration faq",
        "compare providers", "submit a provider", "authentication", "hall of fame", "guides",
    }
    if low in generic:
        return None
    fragments = (
        " faq", "documentation", "deprecation", "batch api", "flex tier",
        "models page", "pricing page", "available regions", "data usage",
        "service tiers", "limits documentation", "registration faq",
        "authentication faq", "aws activate", "batch processing", "batch inference",
        "flex processing",
    )
    if any(token in low for token in fragments):
        return None
    if low.startswith(("http://", "https://")) or re.search(r"\.[a-z]{2,}/", low):
        return None
    if any(token in low for token in ("free llm api", "comparison", "directory of", "awesome free")):
        return None
    if not re.search(r"[A-Za-z0-9]", text):
        return None
    return text.rstrip(".")


def _parse_provider_catalogue(text: str, *, source_url: str) -> list[dict[str, str]]:
    """Extract conservative provider leads from a multi-provider catalogue.

    Provider identity must come from a table's provider cell or an explicit
    list/header label. Documentation, pricing, model, FAQ, and similar link
    labels are not provider identities. Catalogue claims remain untrusted
    until verify_provider_leads() confirms them on an official domain.
    """
    source = _canonical_public_url(source_url) or ""
    source_root = _organization_root(source)
    markdown_link = re.compile(r"\[([^]\n]{1,96})\]\((https?://[^)\s]+)\)")
    raw_url = re.compile(r"https?://[^\s)>]+")
    signal_tokens = (
        "api", "llm", "language model", "inference", "model", "provider",
        "free", "endpoint", "token", "credit", "chat",
    )
    rows: list[dict[str, str]] = []

    def external_urls(line: str) -> list[str]:
        raw_values = [m.group(2).rstrip(".,;") for m in markdown_link.finditer(line)]
        if not raw_values:
            raw_values = [m.group(0).rstrip(".,;") for m in raw_url.finditer(line)]
        found: list[str] = []
        for raw in raw_values:
            url = _canonical_public_url(raw)
            if not url:
                continue
            root = _organization_root(url)
            if not root or root == source_root or root in {"github.com", "githubusercontent.com"}:
                continue
            if url not in found:
                found.append(url)
        return found

    def best_url(label: str, urls: list[str]) -> str:
        tokens = set(re.findall(r"[a-z0-9]+", label.lower()))
        def score(url: str) -> tuple[int, int]:
            host = (urlsplit(url).hostname or "").lower()
            stem = _organization_root(url).split(".", 1)[0]
            identity_match = int(stem in tokens) * 2 + int(any(len(t) >= 4 and t in host for t in tokens))
            return identity_match, -len(url)
        return max(urls, key=score)

    for raw_line in str(text).splitlines():
        line = raw_line.strip()
        if "http" not in line.lower() or not any(token in line.lower() for token in signal_tokens):
            continue
        label: str | None = None
        if "|" in line:
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if len(cells) < 2:
                continue
            first_link = markdown_link.search(cells[0])
            first_value = first_link.group(1) if first_link else cells[0]
            label = _provider_label_candidate(first_value)
        else:
            if not re.match(r"^(?:[-+*]\s+|\d+[.)]\s+|#{2,}\s+|\*\*)", line):
                continue
            bold = re.search(r"\*\*([^*]{2,64})\*\*", line)
            if bold:
                label = _provider_label_candidate(bold.group(1))
            if label is None:
                first_link = markdown_link.search(line)
                if first_link:
                    label = _provider_label_candidate(first_link.group(1))
        if label is None:
            continue
        urls = external_urls(line)
        if not urls:
            continue
        url = best_url(label, urls)
        rows.append({
            "provider_label": label,
            "credential_env": _derived_provider_credential_env(label),
            "signup_url": url,
            "source_claim": line[:1200],
        })

    roots = {_organization_root(row["signup_url"]) for row in rows}
    if len(roots) < 2:
        return []
    deduped: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        key = (_organization_root(row["signup_url"]), row["credential_env"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
        if len(deduped) >= 24:
            break
    return deduped


def _organization_root(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    parts = [part for part in host.split(".") if part]
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _matches_official_root(url: str, official_root: str) -> bool:
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    root = str(official_root or "").lower().rstrip(".")
    return bool(root) and (host == root or host.endswith("." + root))


def _provider_search_result_relevant(provider_label: str, text: str) -> bool:
    low = str(text).lower()
    signals = (
        "api", "inference", "model", "pricing", "price", "billing",
        "rate limit", "free tier", "free plan", "free credit", "token",
    )
    if not any(token in low for token in signals):
        return False
    return _provider_page_relevance(provider_label, text) >= 0.32


def _provider_page_relevance(provider_label: str, text: str) -> float:
    low = str(text).lower()
    tokens = [token.lower() for token in re.findall(r"[a-z0-9]+", provider_label) if len(token) >= 4]
    generic = sum(0.16 for token in ("api", "model", "inference", "token", "pricing", "rate limit", "free tier", "billing") if token in low)
    identity = 0.32 if any(token in low for token in tokens[:4]) else 0.0
    return min(1.0, identity + generic)


def _classify_free_status(text: str) -> str:
    low = str(text).lower()
    recurring = (
        "free tier", "free allocation", "free plan", "free mode",
        "at no charge", "free models", "free inference", "requests/day",
    )
    trial = ("free credits", "free credit", "trial credits", "trial credit", "free trial")
    paid = ("paid-only", "paid only", "requires the paid plan", "require the paid plan", "must purchase credits")
    if any(token in low for token in recurring):
        return "RECURRING_FREE"
    if any(token in low for token in trial):
        return "TRIAL_CREDIT"
    if any(token in low for token in paid):
        return "PAID_ONLY"
    return "UNKNOWN"


def _payment_method_required(text: str) -> bool | None:
    low = str(text).lower()
    if any(token in low for token in ("no credit card required", "without a credit card", "no payment method required")):
        return False
    if any(token in low for token in ("credit card required", "payment method required", "requires a payment method", "provide a valid payment method")):
        return True
    return None


def _looks_like_rate_limits(text: str) -> bool:
    low = str(text).lower()
    return any(token in low for token in ("rate limit", "requests per minute", "requests per day", "rpm", "rpd", "tokens per minute", "tpm"))


def _provider_evidence_excerpt(text: str) -> str:
    compact = " ".join(str(text).split())
    low = compact.lower()
    anchors = ("free tier", "free allocation", "free credits", "trial credits", "rate limit", "pricing", "payment method")
    indexes = [low.find(token) for token in anchors if low.find(token) >= 0]
    start = max(0, (min(indexes) if indexes else 0) - 180)
    return compact[start:start + 1800]


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
