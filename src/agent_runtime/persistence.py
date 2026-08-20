from enum import Enum


class StatePersistence(Enum):
    PERSISTENT_SELF = "PERSISTENT_SELF"
    REOBSERVE_ON_START = "REOBSERVE_ON_START"
    EXTERNAL = "EXTERNAL"


PERSISTENCE_POLICY = {
    "identity": StatePersistence.PERSISTENT_SELF,
    "memory": StatePersistence.PERSISTENT_SELF,
    "self_beliefs": StatePersistence.PERSISTENT_SELF,
    "body_topology": StatePersistence.PERSISTENT_SELF,
    "digital_local_config": StatePersistence.PERSISTENT_SELF,
    "camera_reading": StatePersistence.REOBSERVE_ON_START,
    "temperature": StatePersistence.REOBSERVE_ON_START,
    "physical_position": StatePersistence.REOBSERVE_ON_START,
    "battery": StatePersistence.REOBSERVE_ON_START,
    "external_api_availability": StatePersistence.REOBSERVE_ON_START,
}
