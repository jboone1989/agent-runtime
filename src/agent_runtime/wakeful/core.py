"""Always-awake event/deadline core that does not require an LLM to stay alive."""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from .models import (
    AttentionSignal,
    CognitionTrigger,
    NeuralControlSignal,
    NeuralFeedbackEvent,
    PlannedWake,
    StimulusEvent,
    WakeReason,
    WakefulSnapshot,
    WakefulState,
)


class ReflexIntegrator(Protocol):
    def integrate(self, channel: str, magnitude: float, observed_at: datetime) -> Any: ...
    def snapshot(self) -> dict[str, Any]: ...
    def load_snapshot(self, payload: dict[str, Any]) -> None: ...


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


_INPUT_WEIGHTS_BY_KIND = {
    "TALK_MESSAGE": {"social_attention": 1.0},
    "X_MENTION": {"social_attention": 1.0},
    "NETWORK_EVIDENCE": {"opportunity": 0.90},
    "NETWORK_ERROR": {"error_recovery": 1.0, "threat_risk": 0.35},
    "PREDICTION_DUE": {"prediction_due": 1.0},
    "RESOURCE_CHANGE": {"resource_pressure": 1.0},
    "MATERIAL_MARKET_EVENT": {"opportunity": 0.75, "threat_risk": 0.20},
    "ERROR": {"error_recovery": 1.0, "threat_risk": 0.50},
}
_COGNITION_OUTPUT_THRESHOLD = 0.60


class WakefulCore:
    STATE_VERSION = 1

    def __init__(
        self,
        *,
        state_path: Path,
        reflex: ReflexIntegrator,
        clock: Callable[[], datetime] = _utc_now,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.state_path = Path(state_path)
        self.reflex = reflex
        self.clock = clock
        self.monotonic = monotonic
        self._condition = threading.Condition()
        self._plans: dict[str, PlannedWake] = {}
        self._events: list[StimulusEvent] = []
        self._recent: list[StimulusEvent] = []
        self._attention: dict[str, float] = {}
        self._triggers: list[CognitionTrigger] = []
        self._neural_signals: list[NeuralControlSignal] = []
        self._neural_feedback: list[NeuralFeedbackEvent] = []
        self._resource_pressure = "NORMAL"
        self._active_goal_refs: tuple[str, ...] = ()
        self._open_inquiry_refs: tuple[str, ...] = ()
        self._last_cognition_at: datetime | None = None
        self._last_action_at: datetime | None = None
        self._state = WakefulState.AWAKE
        self._service_started_at = self.clock()
        self._load()

    @property
    def state(self) -> WakefulState:
        return self._state

    def set_state(self, state: WakefulState) -> None:
        with self._condition:
            self._state = state
            self._persist()
            self._condition.notify_all()

    def set_resource_pressure(self, pressure: str) -> None:
        with self._condition:
            self._resource_pressure = str(pressure or "UNKNOWN").upper()
            self._persist()

    def set_context(self, *, active_goal_refs=(), open_inquiry_refs=()) -> None:
        with self._condition:
            self._active_goal_refs = tuple(str(x) for x in active_goal_refs)
            self._open_inquiry_refs = tuple(str(x) for x in open_inquiry_refs)
            self._persist()

    def schedule(self, wake: PlannedWake) -> None:
        with self._condition:
            self._plans[wake.wake_id] = wake
            self._persist()
            self._condition.notify_all()

    def cancel(self, wake_id: str) -> bool:
        with self._condition:
            existed = self._plans.pop(wake_id, None) is not None
            if existed:
                self._persist()
                self._condition.notify_all()
            return existed

    def emit(self, event: StimulusEvent, *, enqueue: bool = True) -> AttentionSignal:
        with self._condition:
            inputs = self._neural_inputs(event)
            primary = max(inputs, key=lambda name: (inputs[name], name))
            if any(row.event_id == event.event_id for row in self._recent[-200:]):
                return AttentionSignal(primary, self._attention.get(primary, 0.0), False)
            integrate_many = getattr(self.reflex, "integrate_many", None)
            if callable(integrate_many):
                result = integrate_many(inputs, event.observed_at)
            else:
                result = self.reflex.integrate(primary, inputs[primary], event.observed_at)
            self._refresh_neural_state(event.observed_at)
            if primary not in self._attention:
                self._attention[primary] = float(getattr(result, "membrane", 0.0))
            membrane = float(self._attention.get(primary, getattr(result, "membrane", 0.0)))
            fired = bool(getattr(result, "fired", False))
            signals = self._signals_from_result(event, result)
            self._neural_signals.extend(signals)
            self._neural_signals = self._neural_signals[-200:]
            if enqueue:
                self._events.append(event)
            self._recent.append(event)
            self._recent = self._recent[-200:]
            cognition = [row for row in signals if row.kind == "COGNITION"]
            cognition_weight = min(1.5, sum(row.weight for row in cognition))
            if cognition_weight >= _COGNITION_OUTPUT_THRESHOLD and self._cognition_allowed(event):
                neurons = ",".join(sorted({row.neuron for row in cognition})) or primary
                self._triggers.append(self._trigger(event, neurons, cognition_weight))
            elif fired and not signals and self._cognition_allowed(event):
                # Backward-compatible fallback for a W1 single-channel integrator.
                self._triggers.append(self._trigger(event, primary, self._magnitude(event)))
            self._persist()
            self._condition.notify_all()
            return AttentionSignal(primary, membrane, bool(cognition_weight >= _COGNITION_OUTPUT_THRESHOLD))

    def apply_neural_feedback(self, event: NeuralFeedbackEvent) -> dict[str, Any]:
        """Apply provenance-bound learning without creating a wake or cognition trigger."""
        with self._condition:
            apply_feedback = getattr(self.reflex, "apply_feedback", None)
            if not callable(apply_feedback):
                return {"status": "UNSUPPORTED", "outcome_id": event.outcome_id}
            result = apply_feedback(event)
            payload = dict(result) if isinstance(result, dict) else {"status": "APPLIED"}
            payload.setdefault("outcome_id", event.outcome_id)
            if not any(row.outcome_id == event.outcome_id for row in self._neural_feedback):
                self._neural_feedback.append(event)
                self._neural_feedback = self._neural_feedback[-200:]
            self._refresh_neural_state(event.observed_at)
            self._persist()
            return payload

    def pop_ready(self) -> WakeReason | None:
        with self._condition:
            now = self.clock()
            due = sorted((w for w in self._plans.values() if w.at <= now), key=lambda w: (w.at, w.wake_id))
            if due:
                wake = due[0]
                self._plans.pop(wake.wake_id, None)
                self._persist()
                return WakeReason("PLAN", wake.reason, wake_id=wake.wake_id, task=wake.task)
            if self._events:
                event = self._events.pop(0)
                trigger = next((t for t in self._triggers if t.source_event_id == event.event_id), None)
                self._persist()
                return WakeReason(
                    "STIMULUS", event.kind, event_id=event.event_id,
                    cognition_required=trigger is not None,
                )
            return None

    def wait_for_wake(
        self,
        *,
        watchdog_seconds: float = 1800.0,
        stop_requested: Callable[[], bool] | None = None,
    ) -> WakeReason:
        if watchdog_seconds <= 0:
            raise ValueError("watchdog_seconds must be positive")
        stop_requested = stop_requested or (lambda: False)
        with self._condition:
            self._state = WakefulState.WAITING
            self._persist()
            started = self.monotonic()
            while True:
                if stop_requested():
                    self._state = WakefulState.AWAKE
                    self._persist()
                    return WakeReason("STOP", "stop requested")
                ready = self.pop_ready()
                if ready is not None:
                    self._state = WakefulState.AWAKE
                    self._persist()
                    return ready
                remaining_watchdog = watchdog_seconds - (self.monotonic() - started)
                if remaining_watchdog <= 0:
                    self._state = WakefulState.AWAKE
                    self._persist()
                    return WakeReason("WATCHDOG", "low-frequency runtime watchdog")
                wait_for = min(remaining_watchdog, self._seconds_until_next_plan())
                self._condition.wait(timeout=max(0.01, wait_for))

    def mark_cognition(self, at: datetime | None = None) -> None:
        with self._condition:
            self._last_cognition_at = at or self.clock()
            self._triggers.clear()
            self._persist()

    def mark_action(self, at: datetime | None = None) -> None:
        with self._condition:
            self._last_action_at = at or self.clock()
            self._persist()

    def snapshot(self) -> WakefulSnapshot:
        with self._condition:
            now = self.clock()
            self._refresh_neural_state(now)
            plans = tuple(sorted(self._plans.values(), key=lambda w: (w.at, w.wake_id)))
            return WakefulSnapshot(
                observed_at=now,
                state=self._state,
                next_wake_at=plans[0].at if plans else None,
                next_wake_reason=plans[0].reason if plans else None,
                pending_plans=plans,
                recent_stimuli=tuple(self._recent[-20:]),
                attention=dict(self._attention),
                resource_pressure=self._resource_pressure,
                active_goal_refs=self._active_goal_refs,
                open_inquiry_refs=self._open_inquiry_refs,
                last_cognition_at=self._last_cognition_at,
                last_action_at=self._last_action_at,
                service_started_at=self._service_started_at,
                cognition_triggers=tuple(self._triggers),
                neural_state=self._neural_projection(now),
                neural_control_signals=tuple(self._neural_signals[-50:]),
                recent_neural_feedback=tuple(self._neural_feedback[-50:]),
            )

    def _seconds_until_next_plan(self) -> float:
        if not self._plans:
            return 3600.0
        now = self.clock()
        nearest = min(w.at for w in self._plans.values())
        return max(0.01, (nearest - now).total_seconds())

    @staticmethod
    def _magnitude(event: StimulusEvent) -> float:
        return min(1.5, (
            event.salience * 0.40
            + event.novelty * 0.20
            + event.urgency * 0.30
            + event.persistence * 0.10
        ))

    @classmethod
    def _neural_inputs(cls, event: StimulusEvent) -> dict[str, float]:
        base = cls._magnitude(event)
        if event.attention_channel:
            return {event.attention_channel: base}
        inputs: dict[str, float] = {}
        for channel, scale in _INPUT_WEIGHTS_BY_KIND.get(event.kind, {}).items():
            inputs[channel] = inputs.get(channel, 0.0) + base * scale
        if event.novelty > 0:
            inputs["curiosity"] = inputs.get("curiosity", 0.0) + event.novelty * 0.25
        if event.urgency > 0:
            inputs["task_urgency"] = inputs.get("task_urgency", 0.0) + event.urgency * 0.25
        if not inputs:
            inputs["curiosity"] = base
        return {name: min(1.5, value) for name, value in inputs.items() if value > 0}

    def _refresh_neural_state(self, observed_at: datetime) -> None:
        decay = getattr(self.reflex, "decay_to", None)
        if callable(decay):
            decay(observed_at)
        projection = self._neural_projection(observed_at)
        neurons = projection.get("neurons") if isinstance(projection, dict) else None
        if isinstance(neurons, dict):
            self._attention = {
                str(name): float((row or {}).get("membrane") or 0.0)
                for name, row in neurons.items()
            }

    def _neural_projection(self, observed_at: datetime) -> dict[str, Any]:
        projection = getattr(self.reflex, "projection", None)
        if not callable(projection):
            return {}
        try:
            value = projection(observed_at)
        except TypeError:
            value = projection()
        return dict(value) if isinstance(value, dict) else {}

    @staticmethod
    def _signals_from_result(event: StimulusEvent, result: Any) -> list[NeuralControlSignal]:
        rows = []
        for output in getattr(result, "control_outputs", ()) or ():
            kind = getattr(getattr(output, "kind", None), "value", getattr(output, "kind", ""))
            rows.append(NeuralControlSignal(
                source_event_id=event.event_id,
                neuron=str(getattr(output, "neuron", "")),
                kind=str(kind),
                weight=float(getattr(output, "weight", 0.0)),
                intent=getattr(output, "intent", None),
            ))
        return rows

    def _cognition_allowed(self, event: StimulusEvent) -> bool:
        if self._resource_pressure not in {"CONSERVE", "CRITICAL"}:
            return True
        return event.urgency >= 0.8 or event.kind in {"PREDICTION_DUE", "ERROR"}

    def _trigger(self, event: StimulusEvent, channel: str, priority: float) -> CognitionTrigger:
        return CognitionTrigger(
            trigger_id=f"cog:{event.event_id}",
            created_at=self.clock(),
            channel=channel,
            source_event_id=event.event_id,
            priority=priority,
            reason=f"{channel} threshold crossed by {event.kind}",
        )

    def _persist(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": self.STATE_VERSION,
            "state": self._state.value,
            "plans": [self._wake_payload(w) for w in self._plans.values()],
            "events": [self._event_payload(e) for e in self._events],
            "recent": [self._event_payload(e) for e in self._recent[-200:]],
            "attention": self._attention,
            "resource_pressure": self._resource_pressure,
            "active_goal_refs": list(self._active_goal_refs),
            "open_inquiry_refs": list(self._open_inquiry_refs),
            "last_cognition_at": self._iso(self._last_cognition_at),
            "last_action_at": self._iso(self._last_action_at),
            "service_started_at": self._iso(self._service_started_at),
            "triggers": [self._trigger_payload(t) for t in self._triggers],
            "neural_signals": [asdict(row) for row in self._neural_signals[-200:]],
            "neural_feedback": [self._feedback_payload(row) for row in self._neural_feedback[-200:]],
            "reflex": self.reflex.snapshot(),
        }
        tmp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.state_path)

    def _load(self) -> None:
        if not self.state_path.exists():
            self._persist()
            return
        payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        if int(payload.get("version") or 0) != self.STATE_VERSION:
            raise ValueError("wakeful state version mismatch")
        self._state = WakefulState(payload.get("state") or WakefulState.AWAKE.value)
        self._plans = {w.wake_id: w for w in (self._wake_from(row) for row in payload.get("plans") or [])}
        self._events = [self._event_from(row) for row in payload.get("events") or []]
        self._recent = [self._event_from(row) for row in payload.get("recent") or []]
        self._attention = {str(k): float(v) for k, v in (payload.get("attention") or {}).items()}
        self._resource_pressure = str(payload.get("resource_pressure") or "NORMAL")
        self._active_goal_refs = tuple(str(x) for x in payload.get("active_goal_refs") or [])
        self._open_inquiry_refs = tuple(str(x) for x in payload.get("open_inquiry_refs") or [])
        self._last_cognition_at = self._dt(payload.get("last_cognition_at"))
        self._last_action_at = self._dt(payload.get("last_action_at"))
        self._service_started_at = self._dt(payload.get("service_started_at")) or self.clock()
        self._triggers = [self._trigger_from(row) for row in payload.get("triggers") or []]
        self._neural_signals = [NeuralControlSignal(**dict(row)) for row in payload.get("neural_signals") or []]
        self._neural_feedback = [self._feedback_from(row) for row in payload.get("neural_feedback") or []]
        reflex = payload.get("reflex")
        if isinstance(reflex, dict):
            self.reflex.load_snapshot(reflex)
        

    def interrupt(self) -> None:
        """Wake the condition wait without creating a stimulus or cognition trigger."""
        with self._condition:
            self._condition.notify_all()

    @staticmethod
    def _iso(value: datetime | None) -> str | None:
        return None if value is None else value.astimezone(timezone.utc).isoformat()

    @staticmethod
    def _dt(value: str | None) -> datetime | None:
        return None if not value else datetime.fromisoformat(value).astimezone(timezone.utc)

    @classmethod
    def _wake_payload(cls, wake: PlannedWake) -> dict[str, Any]:
        payload = asdict(wake)
        payload["at"] = cls._iso(wake.at)
        return payload

    @classmethod
    def _wake_from(cls, payload: dict[str, Any]) -> PlannedWake:
        return PlannedWake(
            wake_id=str(payload["wake_id"]),
            at=cls._dt(payload.get("at")) or _utc_now(),
            reason=str(payload["reason"]),
            task=str(payload["task"]),
            relevant_refs=tuple(payload.get("relevant_refs") or ()),
            cancel_conditions=tuple(payload.get("cancel_conditions") or ()),
            preemptible=bool(payload.get("preemptible", True)),
        )

    @classmethod
    def _event_payload(cls, event: StimulusEvent) -> dict[str, Any]:
        payload = asdict(event)
        payload["observed_at"] = cls._iso(event.observed_at)
        return payload

    @classmethod
    def _event_from(cls, payload: dict[str, Any]) -> StimulusEvent:
        raw = dict(payload)
        raw["observed_at"] = cls._dt(raw.get("observed_at")) or _utc_now()
        return StimulusEvent(**raw)

    @classmethod
    def _feedback_payload(cls, event: NeuralFeedbackEvent) -> dict[str, Any]:
        payload = asdict(event)
        payload["observed_at"] = cls._iso(event.observed_at)
        return payload

    @classmethod
    def _feedback_from(cls, payload: dict[str, Any]) -> NeuralFeedbackEvent:
        raw = dict(payload)
        raw["observed_at"] = cls._dt(raw.get("observed_at")) or _utc_now()
        raw["pathway"] = tuple(raw.get("pathway") or ())
        raw["evidence_refs"] = tuple(raw.get("evidence_refs") or ())
        return NeuralFeedbackEvent(**raw)

    @classmethod
    def _trigger_payload(cls, trigger: CognitionTrigger) -> dict[str, Any]:
        payload = asdict(trigger)
        payload["created_at"] = cls._iso(trigger.created_at)
        return payload

    @classmethod
    def _trigger_from(cls, payload: dict[str, Any]) -> CognitionTrigger:
        raw = dict(payload)
        raw["created_at"] = cls._dt(raw.get("created_at")) or _utc_now()
        return CognitionTrigger(**raw)


__all__ = ["ReflexIntegrator", "WakefulCore"]
