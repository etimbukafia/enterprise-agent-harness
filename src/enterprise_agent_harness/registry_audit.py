"""Shared audit contracts for versioned registry mutations."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from threading import RLock
from typing import Protocol

from pydantic import Field, model_validator

from .contracts import AgentLifecycleStatus, ContractModel


class RegistryAuditEvent(ContractModel):
    """Audit record for a registry mutation or deterministic snapshot."""

    schema_version: str = "agent-registry-event.v1"
    event_id: str = Field(min_length=1)
    registry_id: str = Field(min_length=1)
    operation: str = Field(min_length=1)
    component_kind: str = Field(min_length=1)
    component_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    from_status: AgentLifecycleStatus | None = None
    to_status: AgentLifecycleStatus | None = None
    occurred_at: datetime
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def timestamp_is_aware(self) -> RegistryAuditEvent:
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must include timezone information")
        return self


class RegistryAuditSink(Protocol):
    """Storage boundary for registry audit records."""

    def append(self, event: RegistryAuditEvent) -> None:
        """Store one registry event."""


class ListRegistryAuditSink:
    """Thread-safe in-memory registry audit sink for tests and local use."""

    def __init__(self) -> None:
        self.events: list[RegistryAuditEvent] = []
        self._lock = RLock()

    def append(self, event: RegistryAuditEvent) -> None:
        with self._lock:
            self.events.append(deepcopy(event))

    def by_registry(self, registry_id: str) -> list[RegistryAuditEvent]:
        with self._lock:
            return [deepcopy(event) for event in self.events if event.registry_id == registry_id]


__all__ = ["ListRegistryAuditSink", "RegistryAuditEvent", "RegistryAuditSink"]
