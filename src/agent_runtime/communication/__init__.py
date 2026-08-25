"""Persistent local communication channels for a running Agent."""

from .mailbox import (
    MailboxRequest,
    MailboxResponse,
    OperatorNotification,
    RuntimeMailbox,
)

__all__ = [
    "MailboxRequest",
    "MailboxResponse",
    "OperatorNotification",
    "RuntimeMailbox",
]
