"""Bounded public/synthetic trials for verified low-cost LLM providers."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
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
    max_tokens: int = 32


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


_NVIDIA_HOSTED_GENERAL_PREFERENCES: tuple[str, ...] = (
    "openai/gpt-oss-20b",
    "z-ai/glm-5.3-flash",
    "nvidia/nemotron-3-super-120b-a12b",
    "mistralai/mistral-nemotron",
    "meta/muse-glimmer-30b",
)


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
            model=os.environ.get("NVIDIA_TRIAL_MODEL") or "",
            max_tokens=64,
        )
    if credential_env == "OPENROUTER_API_KEY":
        return ProviderTrialTarget(
            credential_env=credential_env,
            base_url="https://openrouter.ai/api/v1",
            model=os.environ.get("OPENROUTER_TRIAL_MODEL") or "",
            include_usage=True,
        )
    return None


def resolve_trial_target(
    credential_env: str, *, api_key: str,
    catalog_transport: Callable[[str, Mapping[str, str], float], Mapping[str, Any]] | None = None,
    timeout_seconds: float = 30.0,
    exclude_models: frozenset[str] | set[str] = frozenset(),
) -> ProviderTrialTarget | None:
    """Resolve current protocol metadata without using vendor identity as a score."""
    target = trial_target_for_credential(credential_env)
    if target is None or target.model:
        return target
    if not api_key:
        raise ValueError("provider trial requires a credential")
    get = catalog_transport or _get_json
    payload = get(
        target.base_url.rstrip("/") + "/models",
        {"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        timeout_seconds,
    )
    rows = payload.get("data")
    if credential_env == "NVIDIA_API_KEY":
        ids = [str(row.get("id") or "") for row in rows if isinstance(row, Mapping)] if isinstance(rows, list) else []
        model = _choose_general_text_model(
            ids, excluded_models=frozenset(exclude_models)
        )
    elif credential_env == "OPENROUTER_API_KEY":
        model = _choose_openrouter_free_text_model(
            rows if isinstance(rows, list) else [], excluded_models=frozenset(exclude_models),
        )
    else:
        model = None
    if not model:
        raise ProviderTrialRequestError("NO_COMPATIBLE_MODEL")
    return replace(target, model=model)




def _choose_openrouter_free_text_model(
    rows: list[Any], *, excluded_models: frozenset[str] = frozenset(),
) -> str | None:
    """Choose the next untried zero-price text->text model from the official catalogue."""
    excluded = ("code", "safety", "guard", "moderation", "vision", "image", "audio")
    candidates: list[tuple[float, str]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        model_id = str(row.get("id") or "").strip()
        low = model_id.lower()
        if (
            not model_id
            or model_id == "openrouter/free"
            or model_id in excluded_models
            or any(token in low for token in excluded)
        ):
            continue
        pricing = row.get("pricing")
        if not isinstance(pricing, Mapping):
            continue
        try:
            if float(pricing.get("prompt", 1)) != 0.0 or float(pricing.get("completion", 1)) != 0.0:
                continue
        except (TypeError, ValueError):
            continue
        architecture = row.get("architecture")
        if not isinstance(architecture, Mapping):
            continue
        inputs = list(architecture.get("input_modalities") or ())
        outputs = list(architecture.get("output_modalities") or ())
        if inputs != ["text"] or outputs != ["text"]:
            continue
        description = str(row.get("description") or "")
        name = str(row.get("name") or "")
        semantic = f"{model_id} {name} {description}".lower()
        specialized = (
            "coding agent", "coding model", "agentic coding",
            "health and medicine", "medicine-focused", "medical-focused",
            "finance-focused", "financial-focused",
            "content safety", "safety model", "moderation model",
            "frontier-reasoning", "large-scale reasoning",
        )
        if any(token in semantic for token in specialized):
            continue
        priority_tokens = ("lightning", "flash", "mini", "small", "nano", "-xs", "_xs")
        priority = 0 if any(token in semantic for token in priority_tokens) else 1
        active = re.findall(r"(\d+(?:\.\d+)?)b active", semantic)
        matches = re.findall(r"(?:^|[-_/ ])(\d+(?:\.\d+)?)b(?:[:\-_/ ,]|$)", semantic)
        size = min((float(value) for value in (active or matches)), default=9999.0)
        candidates.append((priority, size, model_id))
    return min(candidates, default=(0, 0.0, ""))[2] or None

def _choose_general_text_model(
    model_ids: list[str], *, excluded_models: frozenset[str] = frozenset()
) -> str | None:
    """Prefer current hosted general-text NVIDIA targets before legacy catalogue rows."""
    available = {
        str(model_id).strip()
        for model_id in model_ids
        if str(model_id).strip() and str(model_id).strip() not in excluded_models
    }
    for preferred in _NVIDIA_HOSTED_GENERAL_PREFERENCES:
        if preferred in available:
            return preferred

    excluded_tokens = (
        "vision", "embed", "embedding", "safety", "guard", "code", "coder",
        "detector", "rerank", "retriever", "vlm", "video", "image",
        "audio", "speech", "ocr", "diffusion",
    )
    candidates: list[tuple[float, str]] = []
    for model_id in sorted(available):
        low = model_id.lower()
        if any(token in low for token in excluded_tokens):
            continue
        if not (
            "instruct" in low
            or "chat" in low
            or low.endswith("-it")
            or "-it-" in low
            or "gpt-oss" in low
            or "nemotron" in low
            or "glm-" in low
        ):
            continue
        matches = re.findall(r"(?:^|[-_/])(\d+(?:\.\d+)?)b(?:[:\-_/]|$)", low)
        size = min((float(value) for value in matches), default=9999.0)
        candidates.append((size, model_id))
    return min(candidates, default=(0.0, ""))[1] or None


def run_bounded_provider_trial(
    plan: ProviderTrialPlan,
    *,
    api_key: str,
    now: datetime,
    transport: Callable[[str, Mapping[str, str], Mapping[str, Any], float], Mapping[str, Any]] | None = None,
    timeout_seconds: float = 45.0,
    target: ProviderTrialTarget | None = None,
    catalog_transport: Callable[[str, Mapping[str, str], float], Mapping[str, Any]] | None = None,
) -> list[ProviderTrialMeasurement]:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("provider trial time requires timezone awareness")
    if plan.status != "READY_TO_TRIAL":
        raise ValueError("provider trial plan is not ready")
    if plan.data_policy != "PUBLIC_SYNTHETIC_ONLY":
        raise ValueError("provider trial must remain public/synthetic only")
    if not api_key:
        raise ValueError("provider trial requires a credential")
    target = target or resolve_trial_target(
        plan.credential_env, api_key=api_key, catalog_transport=catalog_transport,
    )
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
            "max_tokens": int(target.max_tokens),
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



def _get_json(url: str, headers: Mapping[str, str], timeout: float) -> Mapping[str, Any]:
    request = urllib.request.Request(url, headers=dict(headers), method="GET")
    try:
        with urllib.request.urlopen(request, timeout=float(timeout)) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise ProviderTrialRequestError(f"HTTP_{exc.code}") from exc
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ProviderTrialRequestError("REQUEST_FAILED") from exc
    if not isinstance(payload, Mapping):
        raise ProviderTrialRequestError("INVALID_CATALOG_RESPONSE")
    return payload

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
    "resolve_trial_target", "run_bounded_provider_trial", "trial_target_for_credential",
]
