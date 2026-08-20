from enum import Enum


class IntegrityState(Enum):
    NORMAL = "NORMAL"
    SUSPICIOUS = "SUSPICIOUS"
    CONTAINED = "CONTAINED"
    RECOVERING = "RECOVERING"
    CRITICAL = "CRITICAL"
