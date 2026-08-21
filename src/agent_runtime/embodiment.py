from __future__ import annotations

from dataclasses import dataclass, field

from .actuation.adapter import ExecutorBackedActuator, digital_actuators
from .actuation.physical import CoolingActuator, MovementActuator, PhysicalConserveActuator
from .actuation.registry import ActuatorRegistry
from .body.boundary import BoundaryRelation
from .body.registry import digital_body, physical_body
from .embodiment_descriptor import EmbodimentDescriptor, EmbodimentResolutionContext, UnknownEmbodimentError
from .environment.digital import DigitalEnvironmentAdapter
from .environment.mock_physical import MockPhysicalEnvironment
from .perception.digital import DigitalPerception, PhysicalPerception
from .perception.encoder import SignalEncoder
from .perception.pipeline import PerceptionPipeline
from .sensing.external.provider import ProviderSensor
from .sensing.internal.memory import MemorySensor
from .sensing.internal.process import ProcessSensor
from .sensing.internal.resource import ResourceSensor
from .sensing.internal.security import SecuritySensor
from .sensing.physical import BatterySensor, DistanceSensor, TemperatureSensor
from .sensing.registry import SensorRegistry
from .survival.failover import ProviderPool


@dataclass
class EmbodimentPack:
    environment: object
    sensors: SensorRegistry
    perception: object
    encoder: SignalEncoder
    actuators: ActuatorRegistry
    body: object
    attach_actuators: object = None
    descriptor: EmbodimentDescriptor = field(default_factory=EmbodimentDescriptor)


def _digital_sensors() -> SensorRegistry:
    registry = SensorRegistry()
    for sensor in (
        ResourceSensor(),
        ProcessSensor(),
        ProviderSensor(),
        MemorySensor(),
        SecuritySensor(),
    ):
        registry.register(sensor)
    return registry


def _physical_sensors() -> SensorRegistry:
    registry = SensorRegistry()
    for sensor in (BatterySensor(), DistanceSensor(), TemperatureSensor()):
        registry.register(sensor)
    return registry


def _attach_digital(runtime) -> None:
    for actuator in digital_actuators(
        runtime.executor,
        runtime.self_model,
        lambda: runtime.cycle,
        runtime.clock.now,
    ):
        runtime.actuators.register(actuator)


def _attach_physical(runtime) -> None:
    runtime.actuators.register(MovementActuator(runtime.environment))
    runtime.actuators.register(CoolingActuator(runtime.environment))
    runtime.actuators.register(
        PhysicalConserveActuator(runtime.environment, runtime.regulatory_state)
    )
    runtime.actuators.register(
        ExecutorBackedActuator(
            actuator_id="runtime_actuator",
            kinds={
                "noop",
                "introspect",
                "diagnose",
                "maintain",
                "record",
                "contain",
                "disable_capability",
                "restore_config",
            },
            executor=runtime.executor,
            self_model=runtime.self_model,
            cycle_fn=lambda: runtime.cycle,
            now_fn=runtime.clock.now,
        )
    )


def digital_pack(
    providers: ProviderPool | None = None,
    world=None,
    llm_relation: BoundaryRelation = BoundaryRelation.DEPENDENCY,
) -> EmbodimentPack:
    environment = DigitalEnvironmentAdapter(world=world, pool=providers)
    return EmbodimentPack(
        environment=environment,
        sensors=_digital_sensors(),
        perception=PerceptionPipeline([DigitalPerception()]),
        encoder=SignalEncoder(),
        actuators=ActuatorRegistry(),
        body=digital_body(llm_relation=llm_relation),
        attach_actuators=_attach_digital,
        descriptor=EmbodimentDescriptor(
            embodiment_type="digital",
            environment_type="digital",
            sensor_profile="digital_default",
            perception_profile="digital_default",
            actuator_profile="digital_default",
        ),
    )


def mock_physical_pack() -> EmbodimentPack:
    environment = MockPhysicalEnvironment()
    return EmbodimentPack(
        environment=environment,
        sensors=_physical_sensors(),
        perception=PerceptionPipeline([PhysicalPerception()]),
        encoder=SignalEncoder(),
        actuators=ActuatorRegistry(),
        body=physical_body(),
        attach_actuators=_attach_physical,
        descriptor=EmbodimentDescriptor(
            embodiment_type="mock_physical",
            environment_type="mock_physical",
            sensor_profile="mock_physical_default",
            perception_profile="mock_physical_default",
            actuator_profile="mock_physical_default",
        ),
    )


class BuiltinEmbodimentResolver:
    def resolve(self, descriptor: EmbodimentDescriptor, context: EmbodimentResolutionContext):
        kind = descriptor.embodiment_type or "digital"
        providers = context.bindings.get("provider_pool") if context is not None else None
        if kind == "digital":
            return digital_pack(providers=providers)
        if kind == "mock_physical":
            return mock_physical_pack()
        raise UnknownEmbodimentError(kind)
