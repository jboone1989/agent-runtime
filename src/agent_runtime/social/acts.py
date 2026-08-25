"""Dialogue acts and interaction events. Runtime semantics, not personality."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class DialogueAct(str, Enum):
    REQUEST = "REQUEST"
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    REVOKE = "REVOKE"
    CLARIFY = "CLARIFY"
    VIOLATE = "VIOLATE"
    RENEGOTIATE = "RENEGOTIATE"
    CHALLENGE = "CHALLENGE"
    RESPOND = "RESPOND"
    REFLECT = "REFLECT"


@dataclass
class InteractionEvent:
    event_id: str
    act: DialogueAct
    actor_id: str
    other_id: str
    text: str
    timestamp: datetime | None = None
