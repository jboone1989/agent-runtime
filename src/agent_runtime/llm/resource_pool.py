"""Provider-neutral LLM resource pool with evidence-driven routing and failover."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math
import os
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .deepseek import DeepSeekAnthropicProvider, LLMProviderError
from .openai_compatible import OpenAICompatibleChatProvider


_LIFECYCLES = {"TEMPORARY_FREE", "RECURRING_FREE", "STABLE_PAID"}
_PRICING = {"FREE", "PAID"}
_PROTOCOLS = {"deepseek_anthropic", "openai_chat"}
_CAPABILITY_TIERS = {"STANDARD", "ADVANCED", "FRONTIER"}
_CAPABILITY_TIER_SCORE = {"STANDARD": 0.0, "ADVANCED": 0.16, "FRONTIER": 0.28}
_HIGH_VALUE_TASKS = {
    "self_upgrade", "code_audit", "deep_research", "long_reflection",
    "architecture_review",
}
_PRESSURE_FREE_BIAS = {
    "NORMAL": 0.12,
    "WATCH": 0.20,
    "CONSERVE": 0.30,
    "CRITICAL": 0.40,
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timezone-aware datetime required")
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("resource timestamps require timezone")
    return parsed.astimezone(timezone.utc)


def _bounded(value: float, *, low: float = 0.0, high: float = 1.0) -> float:
    return min(high, max(low, float(value)))


def _reserve_fallback_affordable(policy: "LLMRoutingPolicy") -> bool:
    funds = policy.paid_available_funds
    if funds is None:
        return False
    floor = policy.paid_reserve_floor
    return floor is None or float(funds) > float(floor)


@dataclass(frozen=True)
class LLMResourceSpec:
    resource_id: str
    provider_id: str
    provider_label: str
    model: str
    credential_env: str
    protocol: str
    base_url: str
    lifecycle: str
    pricing_class: str
    reserve: bool = False
    quality_score: float = 0.0
    success_rate: float = 0.0
    mean_latency_ms: float | None = None
    quota_remaining: float | None = None
    quota_limit: float | None = None
    quota_unit: str = "REQUESTS"
    quota_reset_at: str | None = None
    expires_at: str | None = None
    task_classes: tuple[str, ...] = ("general_text",)
    evidence_refs: tuple[str, ...] = ()
    include_usage: bool = True
    metrics_source: str = "EMPIRICAL"
    capability_tier: str = "STANDARD"
    capability_score: float = 0.50
    capability_tags: tuple[str, ...] = ()
    capability_source: str = "UNSPECIFIED"
    windfall_eligible: bool = False

    def __post_init__(self) -> None:
        for name in (
            "resource_id", "provider_id", "provider_label", "model",
            "credential_env", "protocol", "base_url", "lifecycle", "pricing_class",
        ):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"{name} must be non-empty")
        if not str(self.metrics_source or "").strip():
            raise ValueError("metrics_source must be non-empty")
        tier = str(self.capability_tier or "STANDARD").upper()
        if tier not in _CAPABILITY_TIERS:
            raise ValueError("capability_tier must be STANDARD, ADVANCED, or FRONTIER")
        object.__setattr__(self, "capability_tier", tier)
        object.__setattr__(self, "capability_score", _bounded(self.capability_score))
        object.__setattr__(
            self, "capability_tags",
            tuple(dict.fromkeys(str(x) for x in self.capability_tags if str(x))),
        )
        if not str(self.capability_source or "").strip():
            raise ValueError("capability_source must be non-empty")
        if self.protocol not in _PROTOCOLS:
            raise ValueError(f"unsupported llm resource protocol: {self.protocol}")
        if self.lifecycle not in _LIFECYCLES:
            raise ValueError(f"unsupported llm resource lifecycle: {self.lifecycle}")
        if self.pricing_class not in _PRICING:
            raise ValueError(f"unsupported llm pricing class: {self.pricing_class}")
        object.__setattr__(self, "quality_score", _bounded(self.quality_score))
        object.__setattr__(self, "success_rate", _bounded(self.success_rate))
        if self.mean_latency_ms is not None:
            value = float(self.mean_latency_ms)
            if not math.isfinite(value) or value < 0:
                raise ValueError("mean_latency_ms must be finite and non-negative")
            object.__setattr__(self, "mean_latency_ms", value)
        for name in ("quota_remaining", "quota_limit"):
            raw = getattr(self, name)
            if raw is not None:
                value = float(raw)
                if not math.isfinite(value) or value < 0:
                    raise ValueError(f"{name} must be finite and non-negative")
                object.__setattr__(self, name, value)
        unit = str(self.quota_unit or "REQUESTS").upper()
        if unit not in {"REQUESTS", "TOKENS", "CREDITS"}:
            raise ValueError("quota_unit must be REQUESTS, TOKENS, or CREDITS")
        object.__setattr__(self, "quota_unit", unit)
        _parse_time(self.quota_reset_at)
        _parse_time(self.expires_at)
        object.__setattr__(
            self, "task_classes",
            tuple(dict.fromkeys(str(x) for x in self.task_classes if str(x))),
        )
        object.__setattr__(
            self, "evidence_refs",
            tuple(dict.fromkeys(str(x) for x in self.evidence_refs if str(x))),
        )

    @classmethod
    def from_payload(cls, raw: Mapping[str, Any]) -> "LLMResourceSpec":
        payload = dict(raw)
        payload["task_classes"] = tuple(payload.get("task_classes") or ("general_text",))
        payload["evidence_refs"] = tuple(payload.get("evidence_refs") or ())
        payload["capability_tags"] = tuple(payload.get("capability_tags") or ())
        return cls(**payload)

    def to_payload(self) -> dict[str, Any]:
        raw = asdict(self)
        raw["task_classes"] = list(self.task_classes)
        raw["evidence_refs"] = list(self.evidence_refs)
        raw["capability_tags"] = list(self.capability_tags)
        return raw


@dataclass(frozen=True)
class LLMRoutingPolicy:
    routing_mode: str = "FREE_FIRST_RESERVE_PAID"
    pressure: str = "NORMAL"
    prefer_free: bool = True
    minimum_quality: float = 0.70
    max_failovers: int = 4
    preferred_lifecycles: tuple[str, ...] = (
        "TEMPORARY_FREE", "RECURRING_FREE", "STABLE_PAID",
    )
    paid_available_funds: float | None = None
    paid_reserve_floor: float | None = None
    mind_tick: int | None = None
    evidence_refs: tuple[str, ...] = ()
    windfall_active: bool = False
    windfall_bonus: float = 0.30
    high_value_task_classes: tuple[str, ...] = tuple(sorted(_HIGH_VALUE_TASKS))

    def __post_init__(self) -> None:
        pressure = str(self.pressure or "NORMAL").upper()
        if pressure not in _PRESSURE_FREE_BIAS:
            pressure = "NORMAL"
        object.__setattr__(self, "pressure", pressure)
        object.__setattr__(self, "minimum_quality", _bounded(self.minimum_quality))
        object.__setattr__(self, "windfall_bonus", _bounded(self.windfall_bonus, high=0.60))
        object.__setattr__(
            self, "high_value_task_classes",
            tuple(dict.fromkeys(
                str(x) for x in self.high_value_task_classes if str(x)
            )),
        )
        object.__setattr__(self, "max_failovers", max(0, min(int(self.max_failovers), 8)))
        lifecycles = tuple(
            x for x in self.preferred_lifecycles if x in _LIFECYCLES
        )
        object.__setattr__(
            self,
            "preferred_lifecycles",
            lifecycles or ("TEMPORARY_FREE", "RECURRING_FREE", "STABLE_PAID"),
        )
        object.__setattr__(
            self, "evidence_refs",
            tuple(dict.fromkeys(str(x) for x in self.evidence_refs if str(x))),
        )

    @classmethod
    def from_payload(cls, raw: Mapping[str, Any]) -> "LLMRoutingPolicy":
        payload = dict(raw)
        payload["preferred_lifecycles"] = tuple(
            payload.get("preferred_lifecycles")
            or ("TEMPORARY_FREE", "RECURRING_FREE", "STABLE_PAID")
        )
        payload["evidence_refs"] = tuple(payload.get("evidence_refs") or ())
        payload["high_value_task_classes"] = tuple(
            payload.get("high_value_task_classes") or tuple(sorted(_HIGH_VALUE_TASKS))
        )
        allowed = {
            "routing_mode", "pressure", "prefer_free", "minimum_quality",
            "max_failovers", "preferred_lifecycles", "paid_available_funds",
            "paid_reserve_floor", "mind_tick", "evidence_refs",
            "windfall_active", "windfall_bonus", "high_value_task_classes",
        }
        return cls(**{k: v for k, v in payload.items() if k in allowed})


@dataclass
class LLMResourceHealth:
    successes: int = 0
    failures: int = 0
    consecutive_failures: int = 0
    cooldown_until: str | None = None
    last_error_code: str = ""
    last_success_at: str | None = None
    last_failure_at: str | None = None
    consumed_quota: float = 0.0
    quota_window_signature: str = ""

    @classmethod
    def from_payload(cls, raw: Mapping[str, Any]) -> "LLMResourceHealth":
        allowed = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in raw.items() if k in allowed})

    @property
    def observed_success_rate(self) -> float | None:
        total = self.successes + self.failures
        return (self.successes / total) if total else None


@dataclass(frozen=True)
class LLMRoutingDecision:
    decision_id: str
    observed_at: str
    operation: str
    task_class: str
    selected_resource_id: str | None
    attempted_resource_ids: tuple[str, ...]
    result: str
    pressure: str
    mind_tick: int | None
    reason: str
    evidence_refs: tuple[str, ...] = ()
    prompt_sha256: str = ""

    def to_payload(self) -> dict[str, Any]:
        raw = asdict(self)
        raw["attempted_resource_ids"] = list(self.attempted_resource_ids)
        raw["evidence_refs"] = list(self.evidence_refs)
        return raw


class ResourcePoolProvider:
    """Route calls across temporary/free resources while keeping paid stable reserve."""

    def __init__(
        self,
        resources: Iterable[LLMResourceSpec],
        *,
        policy_path: Path | None = None,
        state_dir: Path | None = None,
        provider_factory: Callable[[LLMResourceSpec], Any] | None = None,
        manifest_path: Path | None = None,
        now: Callable[[], datetime] = _utcnow,
    ) -> None:
        self.resources = tuple(resources)
        if not self.resources:
            raise LLMProviderError("LLM resource pool has no resources")
        ids = [item.resource_id for item in self.resources]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate LLM resource_id")
        self.policy_path = Path(policy_path) if policy_path else None
        self.state_dir = Path(state_dir) if state_dir else None
        self.provider_factory = provider_factory or self._provider_from_environment
        self.manifest_path = Path(manifest_path) if manifest_path else None
        self._manifest_mtime_ns = (
            self.manifest_path.stat().st_mtime_ns
            if self.manifest_path is not None and self.manifest_path.exists()
            else None
        )
        self.now = now
        self._health = self._load_health()
        self._providers: dict[str, Any] = {}

    async def complete(self, prompt: str, **kwargs: Any) -> str:
        return await self._run("complete", prompt, None, kwargs)

    async def structured(
        self, prompt: str, schema: dict[str, Any], **kwargs: Any
    ) -> dict[str, Any]:
        return await self._run("structured", prompt, schema, kwargs)

    async def embed(self, text: str, **kwargs: Any) -> list[float]:
        return await self._run("embed", text, None, kwargs)

    async def _run(
        self,
        operation: str,
        prompt: str,
        schema: dict[str, Any] | None,
        kwargs: dict[str, Any],
    ) -> Any:
        self._refresh_resources()
        clean_kwargs = dict(kwargs)
        task_class = str(clean_kwargs.pop("_routing_task_class", "general_text"))
        requested_quality = clean_kwargs.pop("_minimum_quality", None)
        attempt_timeout = clean_kwargs.pop("_attempt_timeout_seconds", None)
        if attempt_timeout is not None:
            attempt_timeout = max(1.0, float(attempt_timeout))
        policy = self._load_policy()
        if requested_quality is not None:
            policy = LLMRoutingPolicy.from_payload({
                **asdict(policy),
                "minimum_quality": max(
                    policy.minimum_quality, _bounded(float(requested_quality))
                ),
            })
        ranked = self._ranked(task_class=task_class, policy=policy)
        if not ranked:
            raise LLMProviderError("no viable LLM resource is currently available")
        attempted: list[str] = []
        errors: list[str] = []
        limit = min(len(ranked), 1 + policy.max_failovers)
        attempt_rows = list(ranked[:limit])
        if (
            policy.routing_mode == "FREE_FIRST_RESERVE_PAID"
            and policy.max_failovers > 0
            and len(attempt_rows) > 1
            and _reserve_fallback_affordable(policy)
            and not any(
                spec.reserve and spec.pricing_class == "PAID"
                for spec, _score, _reason in attempt_rows
            )
        ):
            reserve = next(
                (
                    row
                    for row in ranked[limit:]
                    if row[0].reserve and row[0].pricing_class == "PAID"
                ),
                None,
            )
            if reserve is not None:
                attempt_rows[-1] = reserve
        if operation == "structured" and _reserve_fallback_affordable(policy):
            reserve_index = next(
                (
                    index
                    for index, (spec, _score, _reason) in enumerate(attempt_rows)
                    if spec.reserve and spec.pricing_class == "PAID"
                ),
                None,
            )
            if reserve_index is not None and reserve_index > 1:
                reserve_row = attempt_rows.pop(reserve_index)
                attempt_rows.insert(1, reserve_row)
        for spec, _score, reason in attempt_rows:
            attempted.append(spec.resource_id)
            try:
                provider = self._providers.get(spec.resource_id)
                if provider is None:
                    provider = self.provider_factory(spec)
                    self._providers[spec.resource_id] = provider
                attempt_kwargs = dict(clean_kwargs)
                if attempt_timeout is not None:
                    attempt_kwargs["timeout_seconds"] = attempt_timeout
                if operation == "structured":
                    result = await provider.structured(prompt, schema or {}, **attempt_kwargs)
                elif operation == "embed":
                    result = await provider.embed(prompt, **attempt_kwargs)
                else:
                    result = await provider.complete(prompt, **attempt_kwargs)
                self._record_success(spec, provider)
                self._record_decision(
                    operation=operation,
                    task_class=task_class,
                    selected=spec,
                    attempted=attempted,
                    result="COMPLETED",
                    policy=policy,
                    reason=reason,
                    prompt=prompt,
                )
                return result
            except Exception as exc:
                code = self._error_code(exc)
                errors.append(f"{spec.resource_id}:{code}")
                self._record_failure(spec, code)
                continue
        self._record_decision(
            operation=operation,
            task_class=task_class,
            selected=None,
            attempted=attempted,
            result="FAILED_OVER_ALL_ELIGIBLE_RESOURCES",
            policy=policy,
            reason=";".join(errors)[:1000],
            prompt=prompt,
        )
        raise LLMProviderError(
            "all eligible LLM resources failed without exposing provider responses"
        )

    def _ranked(
        self, *, task_class: str, policy: LLMRoutingPolicy
    ) -> list[tuple[LLMResourceSpec, float, str]]:
        now = self.now()
        candidates: list[tuple[LLMResourceSpec, float, str]] = []
        free_present = False
        for spec in self.resources:
            eligible, reason = self._eligible(
                spec, task_class=task_class, policy=policy, now=now
            )
            if not eligible:
                continue
            if spec.pricing_class == "FREE":
                free_present = True
            candidates.append((spec, 0.0, reason))
        ranked: list[tuple[LLMResourceSpec, float, str]] = []
        lifecycle_rank = {
            value: idx for idx, value in enumerate(policy.preferred_lifecycles)
        }
        for spec, _score, eligibility_reason in candidates:
            health = self._health.setdefault(spec.resource_id, LLMResourceHealth())
            observed = health.observed_success_rate
            reliability = (
                _bounded((spec.success_rate * 0.7) + (observed * 0.3))
                if observed is not None
                else spec.success_rate
            )
            quality = spec.quality_score
            latency_score = (
                0.5
                if spec.mean_latency_ms is None
                else 1.0 / (1.0 + (spec.mean_latency_ms / 5000.0))
            )
            cost_score = 1.0 if spec.pricing_class == "FREE" else 0.25
            high_value = task_class in set(policy.high_value_task_classes)
            direct_task_match = task_class in spec.task_classes
            score = (
                quality * 0.45
                + reliability * 0.25
                + latency_score * 0.05
                + cost_score * 0.25
            )
            if high_value:
                score += _CAPABILITY_TIER_SCORE[spec.capability_tier]
                score += spec.capability_score * 0.16
                if direct_task_match:
                    score += 0.12
                if (
                    policy.windfall_active
                    and spec.windfall_eligible
                    and spec.pricing_class == "FREE"
                ):
                    score += policy.windfall_bonus
            elif spec.windfall_eligible and spec.quota_remaining is not None:
                # Preserve scarce high-capability free quota for work that can exploit it.
                score -= 0.04
            if policy.prefer_free and spec.pricing_class == "FREE":
                score += _PRESSURE_FREE_BIAS[policy.pressure]
            if spec.reserve and free_present:
                score -= 0.12
                if (
                    policy.paid_available_funds is not None
                    and policy.paid_reserve_floor is not None
                    and float(policy.paid_available_funds) <= float(policy.paid_reserve_floor)
                ):
                    score -= 0.15
            expires = _parse_time(spec.expires_at)
            if (
                spec.pricing_class == "FREE"
                and expires is not None
                and timedelta(0) < (expires - now) <= timedelta(days=7)
            ):
                score += 0.03
            score -= 0.02 * lifecycle_rank.get(spec.lifecycle, 99)
            ranked.append(
                (
                    spec,
                    round(score, 6),
                    (
                        f"utility={score:.4f};quality={quality:.3f};"
                        f"reliability={reliability:.3f};pricing={spec.pricing_class};"
                        f"capability={spec.capability_tier}:{spec.capability_score:.3f};"
                        f"windfall={int(spec.windfall_eligible)};"
                        f"pressure={policy.pressure};{eligibility_reason}"
                    ),
                )
            )
        return sorted(
            ranked,
            key=lambda row: (
                -row[1],
                lifecycle_rank.get(row[0].lifecycle, 99),
                row[0].resource_id,
            ),
        )

    def _eligible(
        self,
        spec: LLMResourceSpec,
        *,
        task_class: str,
        policy: LLMRoutingPolicy,
        now: datetime,
    ) -> tuple[bool, str]:
        if not str(os.environ.get(spec.credential_env) or "").strip():
            return False, "credential_missing"
        if task_class not in spec.task_classes and "general_text" not in spec.task_classes:
            return False, "task_class_mismatch"
        if spec.quality_score < policy.minimum_quality and not spec.reserve:
            return False, "below_quality_floor"
        if spec.success_rate < 0.45 and not spec.reserve:
            return False, "below_reliability_floor"
        expires = _parse_time(spec.expires_at)
        if expires is not None and expires <= now:
            return False, "expired"
        health = self._health.setdefault(spec.resource_id, LLMResourceHealth())
        reset_at = _parse_time(spec.quota_reset_at)
        quota_signature = (
            f"{spec.quota_remaining}|{spec.quota_limit}|{spec.quota_unit}|{spec.quota_reset_at}"
        )
        if health.quota_window_signature != quota_signature:
            health.consumed_quota = 0.0
            health.quota_window_signature = quota_signature
            self._persist_health()
        if (
            spec.quota_remaining is not None
            and (reset_at is None or reset_at > now)
            and (float(spec.quota_remaining) - float(health.consumed_quota)) <= 0
        ):
            return False, "quota_exhausted"
        cooldown = _parse_time(health.cooldown_until)
        if cooldown is not None and cooldown > now:
            return False, "runtime_cooldown"
        return True, "eligible"

    def _refresh_resources(self) -> None:
        if self.manifest_path is None or not self.manifest_path.exists():
            return
        try:
            mtime = self.manifest_path.stat().st_mtime_ns
        except OSError:
            return
        if self._manifest_mtime_ns == mtime:
            return
        try:
            resources = load_resource_pool_manifest(self.manifest_path)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return
        if not resources:
            return
        old_ids = {item.resource_id for item in self.resources}
        new_ids = {item.resource_id for item in resources}
        self.resources = tuple(resources)
        self._manifest_mtime_ns = mtime
        for removed in old_ids - new_ids:
            self._providers.pop(removed, None)

    def _provider_from_environment(self, spec: LLMResourceSpec) -> Any:
        key = str(os.environ.get(spec.credential_env) or "").strip()
        if not key:
            raise LLMProviderError(
                f"credential environment is unavailable: {spec.credential_env}"
            )
        if spec.protocol == "deepseek_anthropic":
            return DeepSeekAnthropicProvider(
                api_key=key, model=spec.model, base_url=spec.base_url
            )
        if spec.protocol == "openai_chat":
            return OpenAICompatibleChatProvider(
                api_key=key,
                model=spec.model,
                base_url=spec.base_url,
                include_usage=spec.include_usage,
            )
        raise LLMProviderError(f"unsupported LLM protocol: {spec.protocol}")

    def _load_policy(self) -> LLMRoutingPolicy:
        if self.policy_path is None or not self.policy_path.exists():
            return LLMRoutingPolicy()
        try:
            raw = json.loads(self.policy_path.read_text(encoding="utf-8"))
            if not isinstance(raw, Mapping):
                raise ValueError
            return LLMRoutingPolicy.from_payload(raw)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return LLMRoutingPolicy()

    def _load_health(self) -> dict[str, LLMResourceHealth]:
        path = self._health_path()
        if path is None or not path.exists():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            rows = raw.get("resources") if isinstance(raw, Mapping) else {}
            if not isinstance(rows, Mapping):
                return {}
            return {
                str(key): LLMResourceHealth.from_payload(value)
                for key, value in rows.items()
                if isinstance(value, Mapping)
            }
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return {}

    def _health_path(self) -> Path | None:
        return (self.state_dir / "health.json") if self.state_dir else None

    def _record_success(self, spec: LLMResourceSpec, provider: Any) -> None:
        health = self._health.setdefault(spec.resource_id, LLMResourceHealth())
        health.successes += 1
        health.consecutive_failures = 0
        health.cooldown_until = None
        health.last_error_code = ""
        health.last_success_at = _iso(self.now())
        if spec.quota_remaining is not None:
            metadata = getattr(provider, "last_metadata", {})
            metadata = metadata if isinstance(metadata, Mapping) else {}
            if spec.quota_unit == "REQUESTS":
                consumed = 1.0
            elif spec.quota_unit == "TOKENS":
                consumed = float(
                    int(metadata.get("prompt_tokens") or 0)
                    + int(metadata.get("completion_tokens") or 0)
                )
            else:
                consumed = float(metadata.get("cost") or 0.0)
            health.consumed_quota += max(0.0, consumed)
        self._persist_health()

    def _record_failure(self, spec: LLMResourceSpec, code: str) -> None:
        health = self._health.setdefault(spec.resource_id, LLMResourceHealth())
        health.failures += 1
        health.consecutive_failures += 1
        health.last_error_code = code
        health.last_failure_at = _iso(self.now())
        now = self.now()
        if code == "HTTP_429":
            cooldown = timedelta(hours=1)
        elif code in {"HTTP_404", "HTTP_410"}:
            cooldown = timedelta(hours=6)
        elif code in {"HTTP_401", "HTTP_403"}:
            cooldown = timedelta(hours=12)
        else:
            cooldown = timedelta(minutes=min(30, 2 ** min(health.consecutive_failures, 4)))
        health.cooldown_until = _iso(now + cooldown)
        self._persist_health()

    @staticmethod
    def _error_code(exc: Exception) -> str:
        code = str(getattr(exc, "code", "") or "").strip()
        if code:
            return code
        text = str(exc)
        for value in ("HTTP_401", "HTTP_403", "HTTP_404", "HTTP_410", "HTTP_429"):
            if value in text:
                return value
        return type(exc).__name__

    def _persist_health(self) -> None:
        path = self._health_path()
        if path is None:
            return
        payload = {
            "schema_version": "llm-resource-health.v1",
            "resources": {
                key: asdict(value) for key, value in sorted(self._health.items())
            },
        }
        self._atomic_json(path, payload)

    def _record_decision(
        self,
        *,
        operation: str,
        task_class: str,
        selected: LLMResourceSpec | None,
        attempted: list[str],
        result: str,
        policy: LLMRoutingPolicy,
        reason: str,
        prompt: str,
    ) -> None:
        if self.state_dir is None:
            return
        now = self.now()
        prompt_hash = "sha256:" + sha256(prompt.encode("utf-8")).hexdigest()
        digest = sha256(
            f"{_iso(now)}\0{operation}\0{task_class}\0{prompt_hash}\0{attempted}".encode()
        ).hexdigest()[:24]
        decision = LLMRoutingDecision(
            decision_id="llm-route:" + digest,
            observed_at=_iso(now),
            operation=operation,
            task_class=task_class,
            selected_resource_id=selected.resource_id if selected else None,
            attempted_resource_ids=tuple(attempted),
            result=result,
            pressure=policy.pressure,
            mind_tick=policy.mind_tick,
            reason=reason,
            evidence_refs=tuple(
                dict.fromkeys(
                    [
                        *policy.evidence_refs,
                        *(selected.evidence_refs if selected else ()),
                    ]
                )
            ),
            prompt_sha256=prompt_hash,
        )
        folder = self.state_dir / "decisions"
        folder.mkdir(parents=True, exist_ok=True)
        self._atomic_json(folder / (digest + ".json"), decision.to_payload())
        self._atomic_json(self.state_dir / "latest.json", decision.to_payload())

    @staticmethod
    def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, path)


def load_resource_pool_manifest(path: Path) -> tuple[LLMResourceSpec, ...]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError("LLM resource pool manifest must be an object")
    rows = raw.get("resources")
    if not isinstance(rows, list):
        raise ValueError("LLM resource pool manifest resources must be an array")
    return tuple(
        LLMResourceSpec.from_payload(row)
        for row in rows
        if isinstance(row, Mapping)
    )


def load_resource_pool_provider_from_environment() -> ResourcePoolProvider | None:
    raw_path = str(os.environ.get("AGENT_LLM_RESOURCE_POOL_PATH") or "").strip()
    if not raw_path:
        return None
    path = Path(raw_path)
    if not path.exists():
        return None
    try:
        resources = load_resource_pool_manifest(path)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
    if not resources:
        return None
    policy_raw = str(os.environ.get("AGENT_LLM_ROUTING_POLICY_PATH") or "").strip()
    state_raw = str(os.environ.get("AGENT_LLM_ROUTING_STATE_DIR") or "").strip()
    return ResourcePoolProvider(
        resources,
        policy_path=Path(policy_raw) if policy_raw else None,
        state_dir=Path(state_raw) if state_raw else path.parent / "runtime",
        manifest_path=path,
    )


__all__ = [
    "LLMResourceHealth",
    "LLMResourceSpec",
    "LLMRoutingDecision",
    "LLMRoutingPolicy",
    "ResourcePoolProvider",
    "load_resource_pool_manifest",
    "load_resource_pool_provider_from_environment",
]
