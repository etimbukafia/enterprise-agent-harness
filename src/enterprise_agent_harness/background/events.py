"""Provider-neutral event envelope and trigger contracts."""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any, Literal

from pydantic import Field, model_validator

from ..contracts import ContractModel, utc_now


class EventDisposition(str, Enum):
    """Terminal result of one background event handling attempt."""

    COMPLETED = "completed"
    DUPLICATE = "duplicate"
    FAILED = "failed"
    DEAD_LETTERED = "dead_lettered"
    CANCELLED = "cancelled"
    PENDING_APPROVAL = "pending_approval"
    LEASE_CONFLICT = "lease_conflict"


class FailureCategory(str, Enum):
    """Classification of a failed background handling attempt."""

    RETRYABLE_TRANSIENT = "retryable_transient"
    PERMANENT = "permanent"
    CANCELLED = "cancelled"
    BUDGET_EXHAUSTED = "budget_exhausted"
    APPROVAL_REQUIRED = "approval_required"
    LEASE_LOST = "lease_lost"


class EventEnvelope(ContractModel):
    """One delivered event with stable identity and correlation data.

    The payload is treated as untrusted external data. It is never stored raw
    in traces or audit records; only a deterministic digest is exported.
    """

    schema_version: Literal["agent-event.v1"] = "agent-event.v1"
    event_id: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    source: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: str = Field(default_factory=lambda: utc_now().isoformat())
    correlation_id: str | None = Field(default=None, min_length=1)
    causation_id: str | None = Field(default=None, min_length=1)
    deduplication_key: str | None = Field(default=None, min_length=1)
    principal_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    metadata: dict[str, str] = Field(default_factory=dict)

    @property
    def trigger_id(self) -> str:
        """Return the stable trigger identity derived from type and source."""

        return f"{self.source}:{self.event_type}"

    @property
    def payload_digest(self) -> str:
        """Return a stable, non-reversible digest of the event payload."""

        encoded = json.dumps(
            self.payload,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @property
    def dedup_key(self) -> str:
        """Return the effective deduplication key."""

        if self.deduplication_key is not None:
            return self.deduplication_key
        return hashlib.sha256(
            f"{self.source}\x00{self.event_type}\x00{self.event_id}".encode()
        ).hexdigest()

    @model_validator(mode="after")
    def identity_is_consistent(self) -> EventEnvelope:
        if self.principal_id != self.principal_id.strip():
            raise ValueError("principal_id must not contain surrounding whitespace")
        if self.tenant_id != self.tenant_id.strip():
            raise ValueError("tenant_id must not contain surrounding whitespace")
        return self


class EventTrigger(ContractModel):
    """Routing metadata that identifies which agent handles an event."""

    schema_version: Literal["agent-event-trigger.v1"] = "agent-event-trigger.v1"
    trigger_id: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    source: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    agent_version: str = Field(min_length=1)


__all__ = [
    "EventDisposition",
    "EventEnvelope",
    "EventTrigger",
    "FailureCategory",
]
