"""
Command state machine models.

States: PENDING → SCHEDULED → TRANSMITTING → SENT → ACKED
                                                   ↘ TIMEOUT → RETRY → ACKED
                                                              ↘ DEAD → (FDIR)
"""

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class CommandStatus(str, Enum):
    PENDING = "pending"
    SCHEDULED = "scheduled"
    TRANSMITTING = "transmitting"
    SENT = "sent"
    ACKED = "acked"
    TIMEOUT = "timeout"
    RETRY = "retry"
    DEAD = "dead"


# Allowed next states per current state
_TRANSITIONS: dict[CommandStatus, set[CommandStatus]] = {
    CommandStatus.PENDING: {CommandStatus.SCHEDULED, CommandStatus.DEAD},
    CommandStatus.SCHEDULED: {CommandStatus.TRANSMITTING, CommandStatus.PENDING, CommandStatus.DEAD},
    CommandStatus.TRANSMITTING: {CommandStatus.SENT, CommandStatus.TIMEOUT},
    CommandStatus.SENT: {CommandStatus.ACKED, CommandStatus.TIMEOUT},
    CommandStatus.ACKED: set(),                          # terminal
    CommandStatus.TIMEOUT: {CommandStatus.RETRY, CommandStatus.DEAD},
    CommandStatus.RETRY: {CommandStatus.TRANSMITTING, CommandStatus.DEAD},
    CommandStatus.DEAD: set(),                           # terminal
}

MAX_RETRIES = 3

# Command types that must NEVER be retried (idempotency not guaranteed)
UNSAFE_RETRY_TYPES: frozenset[str] = frozenset({
    "engine_fire",
    "deploy_antenna",
    "deploy_solar_panel",
    "separation",
})


class Command(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    satellite_id: str
    command_type: str
    params: dict[str, Any] = Field(default_factory=dict)
    priority: int = Field(default=5, ge=1, le=10)
    status: CommandStatus = CommandStatus.PENDING
    safe_retry: bool = False          # False = retry blocked for this command
    idempotency_key: str | None = None
    created_by: str | None = None
    retry_count: int = 0
    error_message: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    scheduled_at: datetime | None = None
    sent_at: datetime | None = None
    acked_at: datetime | None = None

    def can_transition_to(self, target: CommandStatus) -> bool:
        return target in _TRANSITIONS[self.status]

    def transition(self, target: CommandStatus, error: str | None = None) -> "Command":
        if not self.can_transition_to(target):
            raise ValueError(
                f"Invalid transition {self.status} → {target} for command {self.id}"
            )
        now = datetime.now(timezone.utc)
        updates: dict[str, Any] = {"status": target, "updated_at": now}

        if target == CommandStatus.SENT:
            updates["sent_at"] = now
        elif target == CommandStatus.ACKED:
            updates["acked_at"] = now
        elif target == CommandStatus.RETRY:
            updates["retry_count"] = self.retry_count + 1
        elif error:
            updates["error_message"] = error

        return self.model_copy(update=updates)

    @property
    def is_terminal(self) -> bool:
        return self.status in (CommandStatus.ACKED, CommandStatus.DEAD)

    @property
    def can_retry(self) -> bool:
        return (
            self.safe_retry
            and self.command_type not in UNSAFE_RETRY_TYPES
            and self.retry_count < MAX_RETRIES
        )
