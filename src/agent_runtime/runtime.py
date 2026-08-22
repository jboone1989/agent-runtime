"""Persistent autonomous loop — Agent Runtime v0.1."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

from .action.executor import ActionExecutor
from .action.policy import ActionPolicy, AuthorizationResult
from .action.proposal import ActionProposal
from .capabilities import (
    AcquisitionTransaction,
    ActivationRecord,
    CapabilityDecision,
    CapabilityGap,
    CapabilityGapDetector,
    CapabilityGapStatus,
    CapabilityInventory,
    CapabilityNeed,
    CapabilityPolicy,
    CapabilityResolver,
    CapabilitySearchRecord,
    NullCapabilityNetworkPort,
    RollbackRecord,
    TaskContract,
    VerificationReceipt,
)
from .capabilities.sources.local import LocalCapabilitySource
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
from .embodiment import BuiltinEmbodimentResolver, EmbodimentPack, digital_pack
from .embodiment_descriptor import EmbodimentDescriptor, EmbodimentResolutionContext, UnknownEmbodimentError
from .executive.attention import AttentionController
from .executive.context import ExecutiveContext
from .executive.controller import ExecutiveController
from .executive.records import ExecutiveDecision
from .executive.working_context import WorkingContext, WorkingContextBuilder
from .experience.refs import stable_ref
from .experience.trace import ExperienceTrace
from .identity.identity import AgentIdentity, create_genesis_identity
from .memory.episodic import Episode
from .memory.store import MemoryStore
from .motivation.default import DefaultMotivationEngine
from .motivation.engine import MotivationEngine, MotivationState
from .observations import Event, Observation
from .planning.legacy_adapter import LegacySimplePlannerAdapter, filter_proposals
from .planning.plan import Plan
from .planning.state import PlanStatus
from .time.waiting import WaitingState
from .perception.base import PerceptionContext
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
from .regulation.escalation import Escalation
from .regulation.integrity import IntegrityRegulator
from .regulation.integrity_state import IntegrityState
from .regulation.observation import build_observation
from .regulation.records import RegulationRecord
from .regulation.resource import ResourceRegulator
from .regulation.service import ServiceRegulator
from .regulation.state import RegulatoryState
from .self_model.introspection import describe_self
from .self_model.model import SelfModel
from .sensing.bus import SignalBus
from .sensing.scheduler import SensorScheduler
from .sensing.sensor import SensorContext
from .actuation.gateway import ActuationGateway
from .environment.base import EnvironmentReadView
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
    embodiment_descriptor: EmbodimentDescriptor | None = None
    # v0.2.2 additions — defaults keep v0.2.1 snapshots restorable.
    capability_inventory: CapabilityInventory = field(default_factory=CapabilityInventory)
    capability_gaps: list[CapabilityGap] = field(default_factory=list)
    # v0.2.3 Executive Foundation — defaults keep v0.2.2 snapshots restorable.
    plans: list[Plan] = field(default_factory=list)
    waiting: list[WaitingState] = field(default_factory=list)
    experience_traces: list[ExperienceTrace] = field(default_factory=list)
    working_context: WorkingContext | None = None
    # v0.2.4 Resolver — defaults keep v0.2.3 snapshots restorable.
    capability_decisions: list[CapabilityDecision] = field(default_factory=list)
    capability_searches: list[CapabilitySearchRecord] = field(default_factory=list)
    # v0.2.5 Acquisition — defaults keep v0.2.4 snapshots restorable.
    acquisition_transactions: list[AcquisitionTransaction] = field(default_factory=list)
    activation_records: list[ActivationRecord] = field(default_factory=list)
    rollback_records: list[RollbackRecord] = field(default_factory=list)
    # v0.2.6 Network delegation — defaults keep v0.2.5 snapshots restorable.
    task_contracts: list[TaskContract] = field(default_factory=list)
    verification_receipts: list[VerificationReceipt] = field(default_factory=list)


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
        embodiment: EmbodimentPack | None = None,
        capability_inventory: CapabilityInventory | None = None,
        capability_gaps: list[CapabilityGap] | None = None,
        plans: list[Plan] | None = None,
        waiting: list[WaitingState] | None = None,
        experience_traces: list[ExperienceTrace] | None = None,
        working_context: WorkingContext | None = None,
        capability_decisions: list[CapabilityDecision] | None = None,
        capability_searches: list[CapabilitySearchRecord] | None = None,
        acquisition_transactions: list[AcquisitionTransaction] | None = None,
        activation_records: list[ActivationRecord] | None = None,
        rollback_records: list[RollbackRecord] | None = None,
        task_contracts: list[TaskContract] | None = None,
        verification_receipts: list[VerificationReceipt] | None = None,
    ) -> None:
        pack = embodiment or digital_pack(providers=providers)
        self.state_dir = Path(state_dir)
        self.config = config
        self.clock = clock or SystemClock()
        self.identity = identity
        self.self_model = self_model
        if pack.body is not None and (not self.self_model.body.components):
            self.self_model.body = pack.body
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
        self.environment = pack.environment
        self.providers = getattr(self.environment, "pool", None) or providers or ProviderPool()
        self.executor = ActionExecutor(providers=self.providers)
        self.executor.environment = self.environment
        self.executor.world = getattr(self.environment, "world", None)
        self.bus = SignalBus()
        self.sensors = pack.sensors
        self.perception = pack.perception
        self.encoder = pack.encoder
        self.actuators = pack.actuators
        self.scheduler = SensorScheduler()
        self.regulatory_state = regulatory_state or RegulatoryState()
        self.coordinator = RegulationCoordinator(
            [ServiceRegulator(), ResourceRegulator(), IntegrityRegulator()]
        )
        self.executor.goals = self.goals
        self.executor.regulatory_state = self.regulatory_state
        self.cycle = cycle
        self.next_action_kind = next_action_kind
        self.last_attribution = last_attribution
        self.last_result: CycleResult | None = None
        self.network = network
        self.running = True
        self.paused = False
        self.embodiment_descriptor = pack.descriptor
        if pack.attach_actuators:
            pack.attach_actuators(self)
        self.actuation_gateway = ActuationGateway(lambda: self.action_policy, self.actuators)
        # Capability Fabric (v0.2.2): inventory, gap detection. No acquisition yet.
        self.capability_inventory = capability_inventory or CapabilityInventory()
        if not self.capability_inventory.entries:
            self.capability_inventory.seed_builtin()
        self.capability_inventory.sync_to_self_model(self.self_model)
        self.capability_gaps: list[CapabilityGap] = list(capability_gaps or [])
        self.capability_gap_detector = CapabilityGapDetector(self.capability_inventory)
        self.plans: list[Plan] = list(plans or [])
        self.waiting: list[WaitingState] = list(waiting or [])
        self.experience_traces: list[ExperienceTrace] = list(experience_traces or [])
        self.working_context = working_context
        self.attention_controller = AttentionController()
        self.context_builder = WorkingContextBuilder()
        self.executive = ExecutiveController()
        self.plan_adapter = LegacySimplePlannerAdapter(self.planner)
        self.last_executive_decision: ExecutiveDecision | None = None
        self.capability_policy = CapabilityPolicy()
        self.capability_resolver = CapabilityResolver(policy=self.capability_policy)
        self.capability_sources = [LocalCapabilitySource(self.capability_inventory)]
        self.capability_decisions: list[CapabilityDecision] = list(capability_decisions or [])
        self.capability_searches: list[CapabilitySearchRecord] = list(capability_searches or [])
        self.acquisition_transactions: list[AcquisitionTransaction] = list(
            acquisition_transactions or []
        )
        self.activation_records: list[ActivationRecord] = list(activation_records or [])
        self.rollback_records: list[RollbackRecord] = list(rollback_records or [])
        self.capability_network = NullCapabilityNetworkPort()
        self.task_contracts: list[TaskContract] = list(task_contracts or [])
        self.verification_receipts: list[VerificationReceipt] = list(
            verification_receipts or []
        )

    @property
    def sense_world(self):
        return getattr(self.environment, "world", None)

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
        embodiment: EmbodimentPack | None = None,
    ) -> AgentRuntime:
        cfg = config or load_config()
        clk = clock or SystemClock()
        pack = embodiment or digital_pack(providers=providers)
        identity = create_genesis_identity(cfg.identity.initial_version, clk)
        policy = autonomy or AutonomyPolicy.from_config(cfg.governance)
        self_model = SelfModel.bootstrap(identity, clk.now(), body=pack.body)
        rt = cls(
            state_dir=Path(state_dir),
            identity=identity,
            self_model=self_model,
            config=cfg,
            autonomy=policy,
            motivation_engine=motivation_engine,
            clock=clk,
            providers=getattr(pack.environment, "pool", None) or providers,
            embodiment=pack,
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
        embodiment_resolver=None,
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
        descriptor = snap.embodiment_descriptor or EmbodimentDescriptor()
        resolver = embodiment_resolver or BuiltinEmbodimentResolver()
        context = EmbodimentResolutionContext(
            bindings={"provider_pool": snap.providers}
        )
        pack = resolver.resolve(descriptor, context)
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
            embodiment=pack,
            capability_inventory=snap.capability_inventory,
            capability_gaps=list(snap.capability_gaps),
            plans=list(snap.plans),
            waiting=list(snap.waiting),
            experience_traces=list(snap.experience_traces),
            working_context=snap.working_context,
            capability_decisions=list(snap.capability_decisions),
            capability_searches=list(snap.capability_searches),
            acquisition_transactions=list(snap.acquisition_transactions),
            activation_records=list(snap.activation_records),
            rollback_records=list(snap.rollback_records),
            task_contracts=list(snap.task_contracts),
            verification_receipts=list(snap.verification_receipts),
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
            embodiment_descriptor=self.embodiment_descriptor,
            capability_inventory=self.capability_inventory,
            capability_gaps=list(self.capability_gaps),
            plans=list(self.plans),
            waiting=list(self.waiting),
            experience_traces=list(self.experience_traces),
            working_context=self.working_context,
            capability_decisions=list(self.capability_decisions),
            capability_searches=list(self.capability_searches),
            acquisition_transactions=list(self.acquisition_transactions),
            activation_records=list(self.activation_records),
            rollback_records=list(self.rollback_records),
            task_contracts=list(self.task_contracts),
            verification_receipts=list(self.verification_receipts),
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
        self.plans = self.plan_adapter.ensure_user_plan(self.goals, self.plans, self.cycle)
        return goal

    @property
    def active_goals(self) -> list[Goal]:
        return [g for g in self.goals if g.status == GoalStatus.ACTIVE]

    @property
    def active_plan(self) -> Plan | None:
        live = [
            plan
            for plan in self.plans
            if plan.status not in {PlanStatus.FAILED, PlanStatus.CANCELLED, PlanStatus.COMPLETED}
        ]
        if live:
            return live[0]
        completed = [plan for plan in self.plans if plan.status == PlanStatus.COMPLETED]
        return completed[-1] if completed else None

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

    def _sensor_environment(self):
        return EnvironmentReadView(self.environment)

    def _perception_context(self, now) -> PerceptionContext:
        return PerceptionContext(
            now=now,
            cycle=self.cycle,
            agent_id=self.identity.agent_id,
            body=self.self_model.body,
        )

    def tick(self) -> CycleResult:
        """Autonomous cycle: sample → perceive → encode → local regulation → optional cognitive step."""
        now = self.clock.now()
        sense_ctx = SensorContext(
            now=now,
            environment=self._sensor_environment(),
            cycle=self.cycle,
            agent_id=self.identity.agent_id,
        )
        raw = self.scheduler.collect(self.sensors, sense_ctx, now)
        percepts = self.perception.perceive(raw, self._perception_context(now))
        signals = self.encoder.encode(percepts)
        self._apply_percepts_to_body(percepts)
        for signal in signals:
            self.bus.publish(signal)
        bundle = self.coordinator.process(
            signals,
            RegulationContext(
                environment=self.environment,
                regulatory_state=self.regulatory_state,
                cycle=self.cycle,
                body=self.self_model.body,
            ),
        )
        denied: list[dict[str, Any]] = []
        executed_local = []
        restore_failed = False
        restore_ok = False
        self.budget.remaining_compute = self.autonomy.max_compute_cost
        self.budget.remaining_financial = self.autonomy.max_financial_cost
        self.executor.goals = self.goals
        self.executor.environment = self.environment
        self.executor.world = getattr(self.environment, "world", None)
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
            outcome, denied_here = self.actuation_gateway.execute(
                proposal,
                self.goals,
                self.budget,
                cycle=self.cycle + 1,
                timestamp=now,
            )
            if outcome is None:
                denied.extend(denied_here)
                continue
            executed_local.append(action)
            self.records.actions.append(_action_record(proposal, self.cycle + 1, now))
            self.records.outcomes.append(outcome)
            if action.kind == "restore_config":
                if outcome.success:
                    restore_ok = True
                else:
                    restore_failed = True
            if not outcome.success:
                denied.append({"action": action.kind, "reason": outcome.description})
        if restore_failed:
            self.regulatory_state.integrity_state = IntegrityState.CRITICAL
            bundle.escalations.append(
                Escalation(
                    code="INTEGRITY_RECOVERY_FAILED",
                    severity=0.95,
                    reason="local restore failed",
                    evidence_refs=[],
                    suggested_dimension="security",
                )
            )
        elif restore_ok and self.regulatory_state.integrity_state == IntegrityState.CONTAINED:
            self.regulatory_state.integrity_state = IntegrityState.RECOVERING
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
            timestamp=now,
            environment=self.environment,
        )
        if bundle.escalations:
            observation.event = Event(
                event_id=new_id("evt"),
                kind="escalation",
                description=";".join(e.code for e in bundle.escalations),
                data={"resolved": False, "codes": [e.code for e in bundle.escalations]},
            )
        elif executed_local:
            observation.event = Event(
                event_id=new_id("evt"),
                kind="regulation",
                description=",".join(a.kind for a in executed_local),
                data={"resolved": True, "actions": [a.kind for a in executed_local]},
            )
        result = self.step(observation)
        result.signals = signals
        result.local_actions = executed_local
        result.escalations = bundle.escalations
        result.regulation_denied = denied
        return result

    def _detect_capability_gaps(self, proposals: list[ActionProposal], now) -> None:
        """Record capability gaps; resolve once the inventory covers them."""
        for gap in self.capability_gaps:
            if gap.status == CapabilityGapStatus.OPEN and self.capability_inventory.usable(
                gap.capability_id
            ):
                gap.status = CapabilityGapStatus.RESOLVED
        open_ids = {
            g.capability_id for g in self.capability_gaps if g.status == CapabilityGapStatus.OPEN
        }
        for gap in self.capability_gap_detector.inspect(
            proposals, cycle=self.cycle, timestamp=now
        ):
            if gap.capability_id in open_ids:
                continue
            open_ids.add(gap.capability_id)
            self.capability_gaps.append(gap)

    def _detect_plan_step_gaps(self, plan: Plan | None, now) -> None:
        if plan is None or plan.current_step is None:
            return
        for gap in self.capability_gaps:
            if gap.status == CapabilityGapStatus.OPEN and self.capability_inventory.usable(
                gap.capability_id
            ):
                gap.status = CapabilityGapStatus.RESOLVED
        open_ids = {
            g.capability_id for g in self.capability_gaps if g.status == CapabilityGapStatus.OPEN
        }
        step = plan.current_step
        for gap in self.capability_gap_detector.inspect_requirements(
            list(step.required_capabilities),
            cycle=self.cycle,
            timestamp=now,
            evidence_refs=[step.step_id, plan.plan_id],
        ):
            if gap.capability_id in open_ids:
                continue
            open_ids.add(gap.capability_id)
            self.capability_gaps.append(gap)

    def _resolve_current_step(self, plan: Plan | None) -> None:
        if plan is None or plan.current_step is None:
            return
        step = plan.current_step
        for requirement in step.required_capabilities:
            if self.capability_inventory.usable(requirement.capability_id):
                continue
            if any(
                decision.capability_id == requirement.capability_id
                and decision.plan_step_id == step.step_id
                for decision in self.capability_decisions
            ):
                continue
            resolution = self.capability_resolver.resolve(
                CapabilityNeed(
                    requirement=requirement,
                    evidence_refs=[step.step_id, plan.plan_id],
                ),
                sources=list(self.capability_sources),
                inventory=self.capability_inventory,
                plan_step_id=step.step_id,
                cycle=self.cycle,
            )
            self.capability_decisions.append(resolution.decision)
            self.capability_searches.append(resolution.search)

    def _apply_percepts_to_body(self, percepts) -> None:
        body = self.self_model.body
        if body is None:
            return
        for percept in percepts:
            if not percept.entity_id or percept.entity_id not in body.components:
                continue
            component = body.components[percept.entity_id]
            remaining = percept.attributes.get("remaining")
            health = percept.attributes.get("health")
            if isinstance(remaining, (int, float)):
                component.health = float(remaining)
            elif isinstance(health, (int, float)):
                component.health = float(health)
            component.state.update({k: v for k, v in percept.attributes.items()})

    def run(self, ticks: int = 1) -> list[CycleResult]:
        results: list[CycleResult] = []
        for _ in range(ticks):
            if not self.running:
                break
            if self.paused:
                continue
            results.append(self.tick())
        return results

    def run_forever(self, *, max_ticks: int | None = None, idle_seconds: float = 0.0) -> None:
        self.running = True
        self.paused = False
        ticks = 0
        while self.running:
            if not self.paused:
                self.tick()
                ticks += 1
                if max_ticks is not None and ticks >= max_ticks:
                    break
            self.clock.sleep(idle_seconds)

    def stop(self) -> None:
        self.running = False

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False
        self.running = True

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
            evidence=[obs.observation_id, *list(obs.evidence_refs)],
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
        self.plans = self.plan_adapter.ensure_user_plan(self.goals, self.plans, self.cycle)

        # Reset per-cycle spend against policy ceilings.
        self.budget.remaining_compute = self.autonomy.max_compute_cost
        self.budget.remaining_financial = self.autonomy.max_financial_cost
        plan = self.active_plan
        self._resolve_current_step(plan)
        self._detect_plan_step_gaps(plan, now)
        attention = self.attention_controller.allocate(
            motivation, obs.event, _attention_candidates(motivation, obs, self.memory)
        )
        self.working_context = self.context_builder.build(
            agent_id=self.identity.agent_id,
            logical_cycle=self.cycle,
            timestamp=now,
            goal_ids=[g.goal_id for g in self.active_goals],
            plan_id=None if plan is None else plan.plan_id,
            attention=attention,
            token_budget=int(self.budget.remaining_compute * 1000),
            compute_budget=self.budget.remaining_compute,
            open_gap_ids=[
                gap.capability_id
                for gap in self.capability_gaps
                if gap.status == CapabilityGapStatus.OPEN
            ],
        )
        exec_decision = self.executive.deliberate(
            ExecutiveContext(
                agent_id=self.identity.agent_id,
                logical_cycle=self.cycle,
                timestamp=now,
                plan=plan,
                motivation=motivation,
                event=obs.event,
                working_context=self.working_context,
                usable_capability_ids=self.capability_inventory.usable_ids(),
            )
        )
        self.last_executive_decision = exec_decision
        wait = None
        if plan is not None:
            plan, wait = self.plan_adapter.apply_decision(plan, exec_decision, self.cycle)
        if wait is not None:
            self.waiting = [item for item in self.waiting if item.plan_id != wait.plan_id] + [wait]
        elif exec_decision.continue_plan and plan is not None:
            self.waiting = [item for item in self.waiting if item.plan_id != plan.plan_id]

        proposals = self.planner.plan(self.goals, motivation)
        proposals = filter_proposals(proposals, plan, exec_decision)
        self._detect_capability_gaps(proposals, now)
        action_records: list[ActionRecord] = []
        outcomes: list[OutcomeRecord] = []
        authorized: list[ActionProposal] = []
        denied_all: list[dict[str, Any]] = []
        for proposal in proposals:
            outcome, denied_here = self.actuation_gateway.execute(
                proposal,
                self.goals,
                self.budget,
                cycle=self.cycle,
                timestamp=now,
            )
            if outcome is None:
                denied_all.extend(denied_here)
                continue
            authorized.append(proposal)
            action_records.append(_action_record(proposal, self.cycle, now))
            outcomes.append(outcome)
        authorization = AuthorizationResult(authorized=authorized, denied=denied_all)
        decision = make_decision(
            cycle=self.cycle,
            timestamp=now,
            goal_ids=[g.goal_id for g in self.active_goals],
            proposals=proposals,
            authorization=authorization,
            viability_score=viability.score,
            motivation_urgency=motivation.urgency,
        )

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
        executed_kinds = [rec.kind for rec in action_records]
        self.plan_adapter.advance(plan, executed_kinds)
        self.experience_traces.append(
            ExperienceTrace(
                trace_id=stable_ref(
                    "xtr", self.identity.agent_id, self.cycle, exec_decision.decision_id
                ),
                agent_id=self.identity.agent_id,
                logical_cycle=self.cycle,
                observed_at=now,
                effective_at=now,
                goal_refs=[g.goal_id for g in self.active_goals],
                plan_ref=None if plan is None else plan.plan_id,
                step_ref=exec_decision.selected_step_id,
                decision_ref=exec_decision.decision_id,
                action_refs=[rec.action_id for rec in action_records],
                outcome_refs=[out.outcome_id for out in outcomes],
                caused_by=list(exec_decision.evidence_refs),
                evidence_refs=list(exec_decision.evidence_refs),
                confidence=1.0,
            )
        )

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


def _attention_candidates(motivation: MotivationState, obs: Observation, memory: MemoryStore) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()

    def add(ref: str) -> None:
        if ref and ref not in seen:
            seen.add(ref)
            ordered.append(ref)

    if obs.event is not None:
        add(f"sig:{obs.event.event_id}")
    for key in motivation.attention_priority:
        add(key)
    for episode in memory.episodic.episodes[-8:]:
        add(f"mem:{episode.episode_id}")
    return ordered

