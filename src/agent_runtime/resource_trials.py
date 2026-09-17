"""Bounded public/synthetic trials for verified low-cost LLM providers."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
import re
import time
from typing import Any, Callable, Mapping
import urllib.error
import urllib.request

from .resource_discovery import ModelTrial, ProviderTrialPlan


class ProviderTrialRequestError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = str(code)


@dataclass(frozen=True)
class ProviderTrialTarget:
    credential_env: str
    base_url: str
    model: str
    include_usage: bool = False


@dataclass(frozen=True)
class ProviderTrialMeasurement:
    trial_id: str
    plan_id: str
    lead_id: str
    provider_label: str
    credential_env: str
    model: str
    task_id: str
    status: str
    observed_at: str
    quality_score: float | None = None
    latency_ms: float | None = None
    observed_cost: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    response_sha256: str = ""
    error_type: str = ""
    error_code: str = ""
    evidence_refs: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        row = asdict(self)
        row["evidence_refs"] = list(self.evidence_refs)
        return row


_BENCHMARKS: tuple[tuple[str, str, str], ...] = (
    (
        "extract_owner",
        "Synthetic record: ticket=K7; severity=high; owner=Mira. Return only the owner name.",
        "MIRA",
    ),
    (
        "sentiment_label",
        "Classify this synthetic sentence: 'The deployment failed twice.' Return only NEGATIVE, NEUTRAL, or POSITIVE.",
        "NEGATIVE",
    ),
    (
        "simple_inference",
        "Synthetic rule: every torin is blue. Object A is a torin. Return only the color of object A.",
        "BLUE",
    ),
)


def trial_target_for_credential(credential_env: str) -> ProviderTrialTarget | None:
    """Return protocol metadata only; provider selection happens elsewhere."""
    if credential_env == "NVIDIA_API_KEY":
        return ProviderTrialTarget(
            credential_env=credential_env,
            base_url="https://integrate.api.nvidia.com/v1",
            model=os.environ.get("NVIDIA_TRIAL_MODEL") or "meta/llama-3.1-8b-instruct",
        )
    if credential_env == "OPENROUTER_API_KEY":
        return ProviderTrialTarget(
            credential_env=credential_env,
            base_url="https://openrouter.ai/api/v1",
            model=os.environ.get("OPENROUTER_TRIAL_MODEL") or "openrouter/free",
            include_usage=True,
        )
    return None


def run_bounded_provider_trial(
    plan: ProviderTrialPlan,
    *,
    api_key: str,
    now: datetime,
    transport: Callable[[str, Mapping[str, str], Mapping[str, Any], float], Mapping[str, Any]] | None = None,
    timeout_seconds: float = 45.0,
) -> list[ProviderTrialMeasurement]:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("provider trial time requires timezone awareness")
    if plan.status != "READY_TO_TRIAL":
        raise ValueError("provider trial plan is not ready")
    if plan.data_policy != "PUBLIC_SYNTHETIC_ONLY":
        raise ValueError("provider trial must remain public/synthetic only")
    if not api_key:
        raise ValueError("provider trial requires a credential")
    target = trial_target_for_credential(plan.credential_env)
    if target is None:
        raise ValueError("provider trial protocol is not implemented")
    post = transport or _post_json
    rows: list[ProviderTrialMeasurement] = []
    max_requests = max(1, min(int(plan.max_requests), len(_BENCHMARKS), 3))
    for task_id, prompt, expected in _BENCHMARKS[:max_requests]:
        trial_id = "model-trial:" + sha256(
            f"{plan.plan_id}\0{target.model}\0{task_id}".encode()
        ).hexdigest()[:24]
        body: dict[str, Any] = {
            "model": target.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": 32,
            "stream": False,
        }
        if target.include_usage:
            body["usage"] = {"include": True}
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        started = time.perf_counter()
        try:
            payload = post(target.base_url.rstrip("/") + "/chat/completions", headers, body, timeout_seconds)
            elapsed = (time.perf_counter() - started) * 1000.0
            text = _response_text(payload)
            normalized = _normalize_answer(text)
            quality = 1.0 if normalized == expected else (0.5 if expected in normalized else 0.0)
            cost = _response_cost(payload)
            prompt_tokens, completion_tokens = _response_token_usage(payload)
            rows.append(ProviderTrialMeasurement(
                trial_id=trial_id,
                plan_id=plan.plan_id,
                lead_id=plan.lead_id,
                provider_label=plan.provider_label,
                credential_env=plan.credential_env,
                model=target.model,
                task_id=task_id,
                status="COMPLETED",
                observed_at=_utc_iso(now),
                quality_score=quality,
                latency_ms=round(elapsed, 3),
                observed_cost=cost,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                response_sha256="sha256:" + sha256(text.encode("utf-8")).hexdigest(),
                evidence_refs=plan.evidence_refs,
            ))
        except Exception as exc:  # The trial boundary sanitizes external provider errors.
            elapsed = (time.perf_counter() - started) * 1000.0
            rows.append(ProviderTrialMeasurement(
                trial_id=trial_id,
                plan_id=plan.plan_id,
                lead_id=plan.lead_id,
                provider_label=plan.provider_label,
                credential_env=plan.credential_env,
                model=target.model,
                task_id=task_id,
                status="FAILED",
                observed_at=_utc_iso(now),
                latency_ms=round(elapsed, 3),
                error_type=type(exc).__name__,
                error_code=str(getattr(exc, "code", "") or ""),
                evidence_refs=plan.evidence_refs,
            ))
    return rows


def model_trials_from_measurements(rows: list[ProviderTrialMeasurement]) -> list[ModelTrial]:
    return [
        ModelTrial(
            trial_id=row.trial_id,
            opportunity_id=row.lead_id,
            task_class="simple_public_text",
            status=row.status,
            quality_score=row.quality_score,
            latency_ms=row.latency_ms,
            observed_cost=row.observed_cost,
            evidence_refs=tuple(dict.fromkeys((
                *row.evidence_refs, row.trial_id,
                *( (row.response_sha256,) if row.response_sha256 else () ),
            ))),
            provider_lead_id=row.lead_id,
            provider_label=row.provider_label,
            model_id=row.model,
            started_at=row.observed_at,
            completed_at=row.observed_at,
            prompt_tokens=row.prompt_tokens,
            completion_tokens=row.completion_tokens,
            error_code=row.error_code or row.error_type,
        )
        for row in rows
    ]


def _post_json(url: str, headers: Mapping[str, str], body: Mapping[str, Any], timeout: float) -> Mapping[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(dict(body), ensure_ascii=False).encode("utf-8"),
        headers=dict(headers),
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=float(timeout)) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise ProviderTrialRequestError(f"HTTP_{exc.code}") from exc
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ProviderTrialRequestError("REQUEST_FAILED") from exc
    if not isinstance(payload, Mapping):
        raise RuntimeError("provider returned invalid JSON")
    return payload


def _response_text(payload: Mapping[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        raise RuntimeError("provider response contains no choices")
    message = choices[0].get("message")
    if not isinstance(message, Mapping):
        raise RuntimeError("provider response contains no message")
    text = str(message.get("content") or "").strip()
    if not text:
        raise RuntimeError("provider returned empty text")
    return text


def _response_cost(payload: Mapping[str, Any]) -> float | None:
    usage = payload.get("usage")
    if not isinstance(usage, Mapping):
        return None
    value = usage.get("cost")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return max(0.0, float(value))
    return None


def _response_token_usage(payload: Mapping[str, Any]) -> tuple[int | None, int | None]:
    usage = payload.get("usage")
    if not isinstance(usage, Mapping):
        return None, None
    def value(name: str) -> int | None:
        raw = usage.get(name)
        if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0:
            return raw
        return None
    return value("prompt_tokens"), value("completion_tokens")


def _normalize_answer(text: str) -> str:
    value = re.sub(r"```(?:text)?|```", "", str(text), flags=re.IGNORECASE).strip()
    value = value.strip(" \t\r\n\"'`.,:;!?[]{}()")
    return " ".join(value.upper().split())


def _utc_iso(now: datetime) -> str:
    return now.astimezone(timezone.utc).replace(microsecond=0).isoformat()


__all__ = [
    "ProviderTrialMeasurement", "ProviderTrialRequestError", "ProviderTrialTarget", "model_trials_from_measurements",
    "run_bounded_provider_trial", "trial_target_for_credential",
]
