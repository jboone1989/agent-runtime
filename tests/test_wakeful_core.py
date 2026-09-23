from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import threading
import time

from agent_runtime.wakeful import PlannedWake, StimulusEvent, WakefulCore, WakefulState


NOW = datetime(2026, 9, 17, 8, 0, tzinfo=timezone.utc)


@dataclass
class _Result:
    fired: bool
    membrane: float


class _Reflex:
    def __init__(self) -> None:
        self.values: dict[str, float] = {}
        self.last_fire: dict[str, datetime] = {}

    def integrate(self, channel, magnitude, observed_at):
        last = self.last_fire.get(channel)
        if last and (observed_at - last).total_seconds() < 60:
            return _Result(False, self.values.get(channel, 0.0))
        value = self.values.get(channel, 0.0) + magnitude
        fired = value >= 1.0
        self.values[channel] = 0.0 if fired else value
        if fired:
            self.last_fire[channel] = observed_at
        return _Result(fired, self.values[channel])

    def snapshot(self):
        return {"values": dict(self.values), "last_fire": {k: v.isoformat() for k, v in self.last_fire.items()}}

    def load_snapshot(self, payload):
        self.values = {str(k): float(v) for k, v in (payload.get("values") or {}).items()}
        self.last_fire = {str(k): datetime.fromisoformat(v) for k, v in (payload.get("last_fire") or {}).items()}


def _event(event_id: str, *, kind="TALK_MESSAGE", urgency=0.5, salience=0.7, novelty=0.6, persistence=0.5):
    return StimulusEvent(
        event_id=event_id, source="test", kind=kind, observed_at=NOW,
        salience=salience, novelty=novelty, urgency=urgency, persistence=persistence,
        payload_ref="ref:test",
    )


def test_weak_stimulus_updates_attention_without_cognition(tmp_path) -> None:
    core = WakefulCore(state_path=tmp_path / "wakeful.json", reflex=_Reflex(), clock=lambda: NOW)
    signal = core.emit(_event("one", salience=0.2, novelty=0.1, urgency=0.1, persistence=0.1))
    assert signal.threshold_crossed is False
    assert core.snapshot().cognition_triggers == ()


def test_related_stimuli_cross_once_then_refractory_blocks_repeat(tmp_path) -> None:
    core = WakefulCore(state_path=tmp_path / "wakeful.json", reflex=_Reflex(), clock=lambda: NOW)
    core.emit(_event("a"))
    core.emit(_event("b"))
    core.emit(_event("c"))
    assert len(core.snapshot().cognition_triggers) == 1
    core.emit(_event("d"))
    assert len(core.snapshot().cognition_triggers) == 1


def test_resource_pressure_defers_low_value_but_not_urgent_cognition(tmp_path) -> None:
    core = WakefulCore(state_path=tmp_path / "wakeful.json", reflex=_Reflex(), clock=lambda: NOW)
    core.set_resource_pressure("CRITICAL")
    for idx in range(3):
        core.emit(_event(f"low-{idx}", urgency=0.2))
    assert core.snapshot().cognition_triggers == ()
    urgent = _event("urgent", kind="ERROR", urgency=1.0, salience=1.0, novelty=1.0, persistence=1.0)
    core.emit(urgent)
    assert any(t.source_event_id == "urgent" for t in core.snapshot().cognition_triggers)


def test_precise_plan_survives_restart(tmp_path) -> None:
    path = tmp_path / "wakeful.json"
    first = WakefulCore(state_path=path, reflex=_Reflex(), clock=lambda: NOW)
    first.schedule(PlannedWake("p17", NOW + timedelta(hours=2), "verify prediction P17", "VERIFY_PREDICTION", ("P17",)))
    restored = WakefulCore(state_path=path, reflex=_Reflex(), clock=lambda: NOW)
    assert restored.snapshot().next_wake_at == NOW + timedelta(hours=2)
    assert restored.snapshot().next_wake_reason == "verify prediction P17"


def test_external_event_preempts_wait_without_periodic_tick(tmp_path) -> None:
    core = WakefulCore(state_path=tmp_path / "wakeful.json", reflex=_Reflex())
    core.schedule(PlannedWake("later", datetime.now(timezone.utc) + timedelta(hours=2), "later", "LATER"))
    result = []
    started = time.monotonic()
    thread = threading.Thread(target=lambda: result.append(core.wait_for_wake(watchdog_seconds=2.0)))
    thread.start()
    time.sleep(0.05)
    core.emit(StimulusEvent(
        event_id="talk-1", source="talk", kind="TALK_MESSAGE", observed_at=datetime.now(timezone.utc),
        salience=0.1, novelty=0.1, urgency=0.1, persistence=0.1,
    ))
    thread.join(timeout=1.0)
    assert result and result[0].kind == "STIMULUS"
    assert time.monotonic() - started < 1.0
    assert core.state == WakefulState.AWAKE


def test_quiet_runtime_can_wait_without_any_model_surface(tmp_path) -> None:
    core = WakefulCore(state_path=tmp_path / "wakeful.json", reflex=_Reflex())
    assert core.snapshot().state == WakefulState.AWAKE
    assert core.snapshot().next_wake_at is None
    assert not hasattr(core, "llm")


def test_pending_event_survives_restart(tmp_path) -> None:
    path = tmp_path / "wakeful.json"
    first = WakefulCore(state_path=path, reflex=_Reflex(), clock=lambda: NOW)
    first.emit(_event("persist-me", salience=0.1, novelty=0.1, urgency=0.1, persistence=0.1))
    restored = WakefulCore(state_path=path, reflex=_Reflex(), clock=lambda: NOW)
    ready = restored.pop_ready()
    assert ready is not None
    assert ready.kind == "STIMULUS"
    assert ready.event_id == "persist-me"


def test_interrupt_allows_stop_condition_to_end_wait(tmp_path) -> None:
    core = WakefulCore(state_path=tmp_path / "wakeful.json", reflex=_Reflex())
    stopping = {"value": False}
    result = []
    thread = threading.Thread(target=lambda: result.append(core.wait_for_wake(
        watchdog_seconds=30.0, stop_requested=lambda: stopping["value"],
    )))
    thread.start()
    time.sleep(0.05)
    stopping["value"] = True
    core.interrupt()
    thread.join(timeout=1.0)
    assert result and result[0].kind == "STOP"


@dataclass(frozen=True)
class _Output:
    neuron: str
    kind: str
    weight: float
    intent: str | None = None


@dataclass
class _GraphResult:
    fired: bool
    membrane: float
    control_outputs: tuple[_Output, ...] = ()


class _Graph:
    def __init__(self, *, outputs=()) -> None:
        self.outputs = tuple(outputs)
        self.currents = {}
        self.membranes = {
            "curiosity": 0.0, "social_attention": 0.0, "task_urgency": 0.0,
        }

    def integrate_many(self, currents, observed_at):
        self.currents = dict(currents)
        for name, value in currents.items():
            if name in self.membranes:
                self.membranes[name] += value
        return _GraphResult(bool(self.outputs), max(self.membranes.values()), self.outputs)

    def decay_to(self, observed_at):
        _ = observed_at

    def projection(self, observed_at=None):
        _ = observed_at
        return {"neurons": {name: {"membrane": value} for name, value in self.membranes.items()}}

    def snapshot(self):
        return {"membranes": dict(self.membranes)}

    def load_snapshot(self, payload):
        self.membranes.update({str(k): float(v) for k, v in payload.get("membranes", {}).items()})


def test_neural_bus_fans_real_stimulus_into_multiple_seed_inputs(tmp_path) -> None:
    graph = _Graph()
    core = WakefulCore(state_path=tmp_path / "wakeful.json", reflex=graph, clock=lambda: NOW)
    core.emit(_event("fanout", kind="TALK_MESSAGE", urgency=0.8, novelty=0.7))
    assert graph.currents["social_attention"] > 0
    assert graph.currents["curiosity"] > 0
    assert graph.currents["task_urgency"] > 0


def test_firing_without_sufficient_behavior_weight_does_not_request_cognition(tmp_path) -> None:
    graph = _Graph(outputs=(_Output("social_attention", "COGNITION", 0.45),))
    core = WakefulCore(state_path=tmp_path / "wakeful.json", reflex=graph, clock=lambda: NOW)
    signal = core.emit(_event("low-control"))
    assert signal.threshold_crossed is False
    assert core.snapshot().cognition_triggers == ()
    assert core.snapshot().neural_control_signals[-1].weight == 0.45


def test_multiple_neural_outputs_compete_before_cognition_authority(tmp_path) -> None:
    graph = _Graph(outputs=(
        _Output("social_attention", "COGNITION", 0.35),
        _Output("task_urgency", "COGNITION", 0.35),
        _Output("social_attention", "ATTENTION", 0.8),
    ))
    core = WakefulCore(state_path=tmp_path / "wakeful.json", reflex=graph, clock=lambda: NOW)
    signal = core.emit(_event("combined"))
    assert signal.threshold_crossed is True
    trigger = core.snapshot().cognition_triggers[-1]
    assert trigger.source_event_id == "combined"
    assert trigger.priority == 0.70
    assert set(trigger.channel.split(",")) == {"social_attention", "task_urgency"}


def test_neural_feedback_bus_is_provider_neutral_and_does_not_wake(tmp_path) -> None:
    from agent_runtime.wakeful import NeuralFeedbackEvent

    class _PlasticGraph(_Graph):
        def __init__(self):
            super().__init__()
            self.feedback_ids = []
        def apply_feedback(self, event):
            self.feedback_ids.append(event.outcome_id)
            self.membranes["social_attention"] += 0.1
            return {"status": "APPLIED", "utility": 0.4}
        def snapshot(self):
            payload = super().snapshot()
            payload["feedback_ids"] = list(self.feedback_ids)
            return payload
        def load_snapshot(self, payload):
            super().load_snapshot(payload)
            self.feedback_ids = list(payload.get("feedback_ids") or [])

    graph = _PlasticGraph()
    core = WakefulCore(state_path=tmp_path / "wakeful.json", reflex=graph, clock=lambda: NOW)
    feedback = NeuralFeedbackEvent(
        outcome_id="outcome:1", observed_at=NOW,
        pathway=("social_attention", "curiosity"), evidence_refs=("evidence:1",),
        information_gain=0.8, goal_progress=0.4,
    )
    result = core.apply_neural_feedback(feedback)
    assert result["status"] == "APPLIED"
    assert graph.feedback_ids == ["outcome:1"]
    assert core.pop_ready() is None
    snap = core.snapshot()
    assert snap.cognition_triggers == ()
    assert snap.recent_stimuli == ()
    assert snap.recent_neural_feedback[-1].outcome_id == "outcome:1"


def test_neural_feedback_metadata_survives_runtime_restart(tmp_path) -> None:
    from agent_runtime.wakeful import NeuralFeedbackEvent

    class _PlasticReflex(_Reflex):
        def apply_feedback(self, event):
            return {"status": "APPLIED", "outcome_id": event.outcome_id}

    path = tmp_path / "wakeful.json"
    first = WakefulCore(state_path=path, reflex=_PlasticReflex(), clock=lambda: NOW)
    first.apply_neural_feedback(NeuralFeedbackEvent(
        outcome_id="persist-feedback", observed_at=NOW,
        pathway=("prediction_due", "task_urgency"), evidence_refs=("evidence:persist",),
        prediction_error=0.25, goal_progress=0.5,
    ))
    restored = WakefulCore(state_path=path, reflex=_PlasticReflex(), clock=lambda: NOW)
    row = restored.snapshot().recent_neural_feedback[-1]
    assert row.outcome_id == "persist-feedback"
    assert row.pathway == ("prediction_due", "task_urgency")
    assert row.evidence_refs == ("evidence:persist",)


def test_event_aware_reflex_receives_exact_stimulus_identity(tmp_path) -> None:
    class _EventAwareGraph(_Graph):
        def __init__(self):
            super().__init__()
            self.event_ids = []
        def integrate_event(self, event, currents):
            self.event_ids.append(event.event_id)
            return self.integrate_many(currents, event.observed_at)

    graph = _EventAwareGraph()
    core = WakefulCore(state_path=tmp_path / "wakeful.json", reflex=graph, clock=lambda: NOW)
    core.emit(_event("action:prediction:1", kind="ACTION_COMMITMENT", urgency=0.6, novelty=0.3))
    assert graph.event_ids == ["action:prediction:1"]
    assert graph.currents["task_urgency"] > 0
    assert graph.currents["prediction_due"] > 0
    assert graph.currents["opportunity"] > 0


def test_neural_feedback_source_event_provenance_survives_restart(tmp_path) -> None:
    from agent_runtime.wakeful import NeuralFeedbackEvent

    class _PlasticReflex(_Reflex):
        def apply_feedback(self, event):
            return {"status": "APPLIED", "outcome_id": event.outcome_id}

    path = tmp_path / "wakeful.json"
    first = WakefulCore(state_path=path, reflex=_PlasticReflex(), clock=lambda: NOW)
    first.apply_neural_feedback(NeuralFeedbackEvent(
        outcome_id="outcome:provenance", source_event_id="action:prediction:1", observed_at=NOW,
        pathway=("prediction_due", "task_urgency"), evidence_refs=("evidence:1",),
        information_gain=0.5,
    ))
    restored = WakefulCore(state_path=path, reflex=_PlasticReflex(), clock=lambda: NOW)
    row = restored.snapshot().recent_neural_feedback[-1]
    assert row.outcome_id == "outcome:provenance"
    assert row.source_event_id == "action:prediction:1"


def test_host_admission_defers_nonessential_plan_without_losing_it(tmp_path) -> None:
    now = [NOW]
    path = tmp_path / "wakeful.json"
    core = WakefulCore(state_path=path, reflex=_Reflex(), clock=lambda: now[0])
    core.set_plan_admission(lambda wake: 300.0 if wake.task == "NEURAL_PLAY" else None)
    core.schedule(PlannedWake("play", NOW, "training", "NEURAL_PLAY", relevant_refs=("motor-decision:1",)))
    core.schedule(PlannedWake("recover", NOW, "resource recovery", "RESOURCE_REVIEW"))
    assert core.pop_ready().task == "RESOURCE_REVIEW"
    assert core.pop_ready() is None
    assert core.snapshot().next_wake_at == NOW + timedelta(seconds=300)
    restored = WakefulCore(state_path=path, reflex=_Reflex(), clock=lambda: NOW + timedelta(seconds=300))
    ready = restored.pop_ready()
    assert ready.task == "NEURAL_PLAY"
    assert ready.relevant_refs == ("motor-decision:1",)
