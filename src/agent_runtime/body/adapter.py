"""Optional mapping from environment channels to body components."""
from __future__ import annotations

CHANNEL_TO_COMPONENT = {
    "process": "runtime_process",
    "memory": "memory_store",
    "security": "runtime_config",
    "battery": "battery",
    "distance_to_obstacle": "range_finder",
    "temperature": "thermal_body",
}
