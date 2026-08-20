"""Persistent autonomous loop — Agent Runtime v0.1."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

from .action.executor import ActionExecutor
from .action.policy import ActionPolicy
from .action.proposal import ActionProposal
from .clock import Clock, SystemClock, new_id
from .cognition.attribution import CausalAttribution, attribute
from .cognition.decision import make_decision
from .cognition.planner import SimplePlanner
from .cognition.reflection import reflect
from .cognition.world_model import WorldModel
from .codec import dump, load
from .config.loader import RuntimeConfig, load_config
from .goals.arbitration import arbitrate
from .goals.generator import GoalGenerator
from .goals.goal import Goal, GoalOrigin, GoalStatus
from .governance.autonomy import AutonomyPolicy
from .governance.budget import ResourceBudget
from .governance.constitution import Constitution
from .identity.identity import AgentIdentity, create_genesis_identity
from .memory.episodic import Episode
from .memory.store import MemoryStore
from .motivation.default import DefaultMotivationEngine
from .motivation.engine import MotivationEngine, MotivationState
from .observations import Observation
from .records import (
    ActionRecord,
    GoalCreated,
    MotivationRecord,
    ObservationRecord,
    OutcomeRecord,
    RecordLog,
)
from .regulation.context import RegulationContext
from .regulation.coordinator import RegulationCoordinator
from .regulation.integrity import IntegrityRegulator
from .regulation.observation import build_observation
from .regulation.records import RegulationRecord
from .regulation.resource import ResourceRegulator
from .regulation.service import ServiceRegulator
from .regulation.state import RegulatoryState
from .self_model.introspection import describe_self
from .self_model.model import SelfModel
from .sensing.bus import SignalBus
from .sensing.factory import default_registry
from .sensing.scheduler import SensorScheduler
from .sensing.sensor import SensorContext
from .sensing.world import SenseWorld
from .survival.failover import ProviderPool
from .viability.evaluator import DefaultViabilityModel
from .viability.state import ViabilityAssessment


@dataclass
class CycleResult:
    cycle: int
    observation: ObservationRecord
    viability: ViabilityAssessment
    motivation: MotivationState
    goals_created: list[Goal]
    decision: Any
    actions: list[ActionRecord]
    outcomes: list[OutcomeRecord]
    endogenous: bool
    endogenous_actions: list[dict[str, Any]]
    attribution: CausalAttribution
    signals: list[Any] = field(default_factory=list)
    local_actions: list[Any] = field(default_factory=list)
    escalations: list[Any] = field(default_factory=list)
    regulation_denied: list[Any] = field(default_factory=list)


@dataclass
class RuntimeSnapshot:
    identity: AgentIdentity
    self_model: SelfModel
    world_state: Any
    goals: list[Goal]
    records: RecordLog
    episodes: list[Episode]
    facts: dict[str, Any]
    procedures: dict[str, str]
    cycle: int
    next_action_kind: str | None
    last_attribution: CausalAttribution | None
    autonomy: AutonomyPolicy
    budget: ResourceBudget
    constitution: Constitution
    providers: ProviderPool
    regulatory_state: RegulatoryState


class AgentRuntime:
    """Continuity of self: identity, models, goals, and loop survive restart."""

    def __init__(
        self,
        *,
        state_dir: Path,
        identity: AgentIdentity,
        self_model: SelfModel,
        config: RuntimeConfig,
        autonomy: AutonomyPolicy,
        motivation_engine: MotivationEngine | None = None,
        clock: Clock | None = None,
        constitution: Constitution | None = None,
        budget: ResourceBudget | None = None,
        world_model: WorldModel | None = None,
        goals: list[Goal] | None = None,
        records: RecordLog | None = None,
        memory: MemoryStore | None = None,
        cycle: int = 0,
        next_action_kind: str | None = None,
        last_attribution: CausalAttribution | None = None,
        network: Any = None,
        providers: ProviderPool | None = None,
        regulatory_state: RegulatoryState | None = None,
        sense_world: SenseWorld | None = None,
    ) -> None:
        self.state_dir = Path(state_dir)
        self.config = config
        self.clock = clock or SystemClock()
        self.identity = identity
        self.self_model = self_model
        self.world_model = world_model or WorldModel()
        self.goals: list[Goal] = goals or []
        self.records = records or RecordLog()
        self.memory = memory or MemoryStore()
        self.autonomy = autonomy
        self.constitution = constitution or Constitution()
        self.budget = budget or ResourceBudget.from_limits(
            autonomy.max_compute_cost, autonomy.max_financial_cost
        )
        self.motivation_engine = motivation_engine or DefaultMotivationEngine()
        self.viability_model = DefaultViabilityModel(config.viability)
        self.goal_generator = GoalGenerator(config.viability.goal_threat_threshold)
        self.planner = SimplePlanner()
        self.action_policy = ActionPolicy(autonomy, self.constitution)
        self.providers = providers or ProviderPool()
        self.executor = ActionExecutor(providers=self.providers)
        self.sense_world = sense_world or SenseWorld()
        self.bus = SignalBus()
        self.sensors = default_registry()
        self.scheduler = SensorScheduler()
        self.regulatory_state = regulatory_state or RegulatoryState()
        self.coordinator = RegulationCoordinator(
            [ServiceRegulator(), ResourceRegulator(), IntegrityRegulator()]
        )
        self.executor.world = self.sense_world
        self.executor.goals = self.goals
        self.executor.regulatory_state = self.regulatory_state
        self.cycle = cycle
        self.next_action_kind = next_action_kind
        self.last_attribution = last_attribution
        self.last_result: CycleResult | None = None
        self.network = network
        self.running = True

    @classmethod
    def bootstrap(
        cls,
        state_dir: str | Path,
        *,
        autonomy: AutonomyPolicy | None = None,
        motivation_engine: MotivationEngine | None = None,
        config: RuntimeConfig | None = None,
        clock: Clock | None = None,
        providers: ProviderPool | None = None,
    ) -> AgentRuntime:
        cfg = config or load_config()
        clk = clock or SystemClock()
        identity = create_genesis_identity(cfg.identity.initial_version, clk)
        policy = autonomy or AutonomyPolicy.from_config(cfg.governance)
        self_model = SelfModel.bootstrap(identity, clk.now())
        rt = cls(
            state_dir=Path(state_dir),
            identity=identity,
            self_model=self_model,
            config=cfg,
            autonomy=policy,
            motivation_engine=motivation_engine,
            clock=clk,
            providers=providers,
        )
        rt.persist()
        return rt

    @classmethod
    def restore(
        cls,
        state_dir: str | Path,
        *,
        motivation_engine: MotivationEngine | None = None,
        config: RuntimeConfig | None = None,
        clock: Clock | None = None,
    ) -> AgentRuntime:
        path = Path(state_dir) / "agent_state.json"
        snap: RuntimeSnapshot = load(path)
        cfg = config or load_config()
        world = WorldModel()
        world.state = snap.world_state
        memory = MemoryStore()
        memory.episodic.episodes = list(snap.episodes)
        memory.semantic.facts = dict(snap.facts)
        memory.procedural.procedures = dict(snap.procedures)
        return cls(
            state_dir=Path(state_dir),
            identity=snap.identity,
            self_model=snap.self_model,
            config=cfg,
            autonomy=snap.autonomy,
            motivation_engine=motivation_engine,
            clock=clock,
            constitution=snap.constitution,
            budget=snap.budget,
            world_model=world,
            goals=list(snap.goals),
            records=snap.records,
            memory=memory,
            cycle=snap.cycle,
            next_action_kind=snap.next_action_kind,
            last_attribution=snap.last_attribution,
            providers=snap.providers,
            regulatory_state=snap.regulatory_state,
        )

    def persist(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        dump(self._snapshot(), self.state_dir / "agent_state.json")

    def _snapshot(self) -> RuntimeSnapshot:
        return RuntimeSnapshot(
            identity=self.identity,
            self_model=self.self_model,
            world_state=self.world_model.state,
            goals=list(self.goals),
            records=self.records,
            episodes=list(self.memory.episodic.episodes),
            facts=dict(self.memory.semantic.facts),
            procedures=dict(self.memory.procedural.procedures),
            cycle=self.cycle,
            next_action_kind=self.next_action_kind,
            last_attribution=self.last_attribution,
            autonomy=self.autonomy,
            budget=self.budget,
            constitution=self.constitution,
            providers=self.providers,
            regulatory_state=self.regulatory_state,
        )

    def submit_goal(self, description: str, reason: str = "operator submitted") -> Goal:
        goal = Goal.create(
            description=description,
            origin=GoalOrigin.USER,
            created_at=self.clock.now(),
            reason=reason,
            priority=0.4,
        )
        self.goals.append(goal)
        rec = GoalCreated(
            record_id=new_id("gcr"),
            cycle=self.cycle,
            timestamp=goal.created_at,
            goal_id=goal.goal_id,
            origin=goal.origin.value,
            description=goal.description,
            reason=goal.reason,
            evidence=[],
        )
        self.records.goals_created.append(rec)
        self.self_model.sync_goals([g.goal_id for g in self.active_goals])
        return goal

    @property
    def active_goals(self) -> list[Goal]:
        return [g for g in self.goals if g.status == GoalStatus.ACTIVE]

    def knows_who_it_is(self) -> bool:
        return (
            self.identity.agent_id == self.self_model.identity.agent_id
            and self.identity.genesis_id == self.self_model.identity.genesis_id
        )

    def knows_what_it_is_doing(self) -> bool:
        return bool(self.active_goals)

    def knows_why(self) -> bool:
        return all(g.reason for g in self.active_goals)

    def knows_what_is_next(self) -> bool:
        return self.next_action_kind is not None

    def tick(self) -> CycleResult:
        """Autonomous cycle: sensors → local regulation → optional cognitive step."""
        now = self.clock.now()
        sense_ctx = SensorContext(
            now=now,
            world=self.sense_world,
            providers=self.providers,
            cycle=self.cycle,
            agent_id=self.identity.agent_id,
        )
        signals = self.scheduler.collect(self.sensors, sense_ctx, now)
        for signal in signals:
            self.bus.publish(signal)
        bundle = self.coordinator.process(
            signals,
            RegulationContext(
                world=self.sense_world,
                providers=self.providers,
                regulatory_state=self.regulatory_state,
                cycle=self.cycle,
            ),
        )
        denied: list[dict[str, Any]] = []
        executed_local = []
        self.budget.remaining_compute = self.autonomy.max_compute_cost
        self.budget.remaining_financial = self.autonomy.max_financial_cost
        self.executor.goals = self.goals
        self.executor.world = self.sense_world
        self.executor.regulatory_state = self.regulatory_state
        for action in bundle.local_actions:
            proposal = ActionProposal(
                kind=action.kind,
                resource=action.resource,
                compute_cost=action.compute_cost,
                financial_cost=action.financial_cost,
                goal_id=None,
                reason=action.reason,
                payload=dict(action.payload),
                trigger="REGULATION",
            )
            authorization = self.action_policy.filter([proposal], self.goals, self.budget)
            if not authorization.authorized:
                denied.extend(authorization.denied)
                continue
            outcome = self.executor.execute(
                proposal, self.self_model, cycle=self.cycle + 1, timestamp=now
            )
            executed_local.append(action)
            self.records.actions.append(_action_record(proposal, self.cycle + 1, now))
            self.records.outcomes.append(outcome)
            if not outcome.success:
                denied.append({"action": action.kind, "reason": outcome.description})
        for decision in bundle.decisions:
            self.records.regulations.append(
                RegulationRecord(
                    record_id=new_id("reg"),
                    cycle=self.cycle + 1,
                    timestamp=now,
                    system=decision.system,
                    reason=decision.reason,
                    local_actions=[a.kind for a in decision.local_actions],
                    escalations=[e.code for e in decision.escalations],
                    confidence=decision.confidence,
                    denied=list(denied) if decision.system == "service" else [],
                )
            )
        self.records.escalations.extend(bundle.escalations)
        observation = build_observation(
            signals=signals,
            escalations=bundle.escalations,
            local_actions=executed_local,
            world=self.sense_world,
            providers=self.providers,
            timestamp=now,
        )
        result = self.step(observation)
        result.signals = signals
        result.local_actions = executed_local
        result.escalations = bundle.escalations
        result.regulation_denied = denied
        return result

    def run(self, ticks: int = 1) -> list[CycleResult]:
        results: list[CycleResult] = []
        for _ in range(ticks):
            if not self.running:
                break
            results.append(self.tick())
        return results

    def step(self, observation: Observation | None = None) -> CycleResult:
        now = self.clock.now()
        self.cycle += 1
        obs = observation or Observation.self_and_world(
            self_metrics=self.self_model.as_self_state(self.identity).metrics or {"compute": 0.8},
            world=dict(self.world_model.state.environment),
            timestamp=now,
        )
        self_changed = self.self_model.update_from_observation(obs)
        world_changed = self.world_model.update(obs)
        attribution = attribute(
            self_changed,
            world_changed,
            other_agent_changed=bool(obs.payload.get("other_agents")),
            evidence_refs=[obs.observation_id],
        )
        self.last_attribution = attribution

        self_state = self.self_model.as_self_state(self.identity)
        viability_state = self.viability_model.observe(self_state, self.world_model.state)
        horizon = timedelta(hours=self.config.runtime.predict_horizon_hours)
        prediction = self.viability_model.predict(viability_state, horizon)
        viability = self.viability_model.evaluate(prediction)

        motivation = self.motivation_engine.process(
            self_model=self.self_model,
            viability=viability,
            world_state=self.world_model.state,
            event=obs.event,
            memory=self.memory.view(),
        )
        for update in motivation.self_model_updates:
            self.self_model.apply_update(
                update.path,
                update.new_value,
                update.reason,
                update.uncertainty,
                update.evidence_refs,
            )
            self.records.self_model_updates.append(update)

        generated = self.goal_generator.generate(
            viability,
            self.goals,
            now,
            evidence=[obs.observation_id],
        )
        for g in generated:
            self.records.goals_created.append(
                GoalCreated(
                    record_id=new_id("gcr"),
                    cycle=self.cycle,
                    timestamp=now,
                    goal_id=g.goal_id,
                    origin=g.origin.value,
                    description=g.description,
                    reason=g.reason,
                    evidence=list(g.evidence_refs),
                )
            )
        self.goals = arbitrate(self.goals, generated, motivation, viability)
        self.self_model.sync_goals([g.goal_id for g in self.active_goals])

        # Reset per-cycle spend against policy ceilings.
        self.budget.remaining_compute = self.autonomy.max_compute_cost
        self.budget.remaining_financial = self.autonomy.max_financial_cost
        proposals = self.planner.plan(self.goals, motivation)
        authorization = self.action_policy.filter(proposals, self.goals, self.budget)
        decision = make_decision(
            cycle=self.cycle,
            timestamp=now,
            goal_ids=[g.goal_id for g in self.active_goals],
            proposals=proposals,
            authorization=authorization,
            viability_score=viability.score,
            motivation_urgency=motivation.urgency,
        )

        action_records: list[ActionRecord] = []
        outcomes: list[OutcomeRecord] = []
        for proposal in authorization.authorized:
            rec = _action_record(proposal, self.cycle, now)
            action_records.append(rec)
            outcome = self.executor.execute(
                proposal, self.self_model, cycle=self.cycle, timestamp=now
            )
            outcomes.append(outcome)

        if authorization.authorized:
            self.next_action_kind = authorization.authorized[0].kind
        elif proposals:
            self.next_action_kind = "blocked"
        else:
            self.next_action_kind = "noop"

        obs_rec = ObservationRecord(
            record_id=new_id("or"),
            cycle=self.cycle,
            timestamp=now,
            observation_id=obs.observation_id,
            source=obs.source,
            payload=obs.payload,
            external_task=obs.external_task,
        )
        mot_rec = MotivationRecord(
            record_id=new_id("mr"),
            cycle=self.cycle,
            timestamp=now,
            valence=motivation.valence,
            arousal=motivation.arousal,
            urgency=motivation.urgency,
            threat=motivation.threat,
            opportunity=motivation.opportunity,
            drives=dict(motivation.drives),
            learning_signal=motivation.learning_signal,
        )
        self.records.observations.append(obs_rec)
        self.records.viability.append(viability)
        self.records.motivations.append(mot_rec)
        self.records.decisions.append(decision)
        self.records.actions.extend(action_records)
        self.records.outcomes.extend(outcomes)

        reflection = reflect(
            cycle=self.cycle, timestamp=now, viability=viability, outcomes=outcomes
        )
        self.records.reflections.append(reflection)
        self.self_model.history_summary = describe_self(self.self_model)
        self.memory.commit(
            timestamp=now,
            summary=reflection.summary,
            payload={"cycle": self.cycle, "decision": decision.decision_id},
        )
        self.memory.semantic.put("who", self.identity.agent_id)
        self.memory.semantic.put("next", self.next_action_kind)

        endogenous = (not obs.external_task) and any(
            g.origin in (GoalOrigin.VIABILITY, GoalOrigin.INTERNAL) for g in generated
        )
        endogenous_actions = []
        if endogenous:
            for rec, outcome in zip(action_records, outcomes, strict=True):
                if rec.trigger == "INTERNAL":
                    endogenous_actions.append(
                        {
                            "trigger": "INTERNAL",
                            "goal_origin": rec.goal_origin or "VIABILITY",
                            "reason": rec.reason,
                            "evidence": rec.evidence,
                            "action": rec.kind,
                            "outcome": outcome.description,
                        }
                    )

        result = CycleResult(
            cycle=self.cycle,
            observation=obs_rec,
            viability=viability,
            motivation=motivation,
            goals_created=generated,
            decision=decision,
            actions=action_records,
            outcomes=outcomes,
            endogenous=endogenous,
            endogenous_actions=endogenous_actions,
            attribution=attribution,
        )
        self.last_result = result
        if self.config.runtime.persist_every_cycle:
            self.persist()
        return result


def _action_record(proposal: ActionProposal, cycle: int, timestamp) -> ActionRecord:
    return ActionRecord(
        action_id=proposal.action_id,
        cycle=cycle,
        timestamp=timestamp,
        kind=proposal.kind,
        resource=proposal.resource,
        goal_id=proposal.goal_id,
        payload=dict(proposal.payload),
        compute_cost=proposal.compute_cost,
        financial_cost=proposal.financial_cost,
        trigger=proposal.trigger,
        goal_origin=proposal.goal_origin,
        reason=proposal.reason,
        evidence=list(proposal.evidence),
    )
