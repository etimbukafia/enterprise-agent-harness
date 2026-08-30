"""Structured trace recording for runtime decisions."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from copy import deepcopy
from datetime import datetime
from threading import RLock
from typing import Any, Protocol

from ..contracts import ExecutionContext, OutcomeStatus
from ..evaluation.contracts import RunTrace, TraceEvent
from ..providers.contracts import ProviderCallMetadata, ProviderCallRecord, ProviderOperation


class TraceSink(Protocol):
    """Storage boundary for structured trace events."""

    def append(self, event: TraceEvent) -> None:
        """Persist one trace event."""


class ListTraceSink:
    """Thread-safe in-memory trace sink for deterministic runs and tests."""

    def __init__(self) -> None:
        self.events: list[TraceEvent] = []
        self._lock = RLock()

    def append(self, event: TraceEvent) -> None:
        with self._lock:
            self.events.append(deepcopy(event))

    def by_execution(self, execution_id: str) -> list[TraceEvent]:
        with self._lock:
            return [deepcopy(event) for event in self.events if event.execution_id == execution_id]


class TraceRecorder:
    """Record stages and export a stable, input-redacted run trace."""

    _BLOCKED_KEY_PARTS = (
        "message",
        "prompt",
        "text",
        "content",
        "output",
        "secret",
        "token",
        "credential",
        "password",
    )

    def __init__(
        self,
        *,
        execution: ExecutionContext,
        input_text: str,
        sink: TraceSink,
        id_factory: Callable[[str], str],
        clock: Callable[[], datetime],
    ) -> None:
        self.execution = execution
        self.sink = sink
        self._id = id_factory
        self._clock = clock
        self._input_fingerprint = fingerprint(input_text)
        self._events: list[TraceEvent] = []
        self._provider_calls: list[ProviderCallRecord] = []

    @property
    def input_fingerprint(self) -> str:
        """Return the non-reversible input fingerprint."""

        return self._input_fingerprint

    def record(
        self, *, stage: str, event_type: str, metadata: dict[str, str] | None = None
    ) -> None:
        """Append one redacted event with a contiguous sequence number."""

        safe_metadata = {
            key: value[:200]
            for key, value in (metadata or {}).items()
            if key and value and not any(part in key.lower() for part in self._BLOCKED_KEY_PARTS)
        }
        event = TraceEvent(
            event_id=self._id("trace_event"),
            execution_id=self.execution.execution_id,
            sequence=len(self._events) + 1,
            stage=stage,
            event_type=event_type,
            occurred_at=self._clock(),
            metadata=safe_metadata,
        )
        self._events.append(event)
        self.sink.append(event)

    def record_provider_call(
        self,
        *,
        operation: ProviderOperation,
        metadata: ProviderCallMetadata,
    ) -> None:
        """Record provider identity, model, latency, retry, and token metadata."""

        self._provider_calls.append(
            ProviderCallRecord(
                operation=operation,
                metadata=metadata.model_copy(deep=True),
            )
        )

    def export(self, *, final_status: OutcomeStatus | None = None) -> RunTrace:
        """Build a machine-readable trace bundle without raw input or output."""

        return RunTrace(
            trace_id=self._id("trace"),
            execution_id=self.execution.execution_id,
            agent_id=self.execution.agent_id,
            agent_version=self.execution.agent_version,
            principal_id=self.execution.principal.principal_id,
            tenant_id=self.execution.principal.tenant_id,
            session_id=self.execution.principal.session_id,
            input_fingerprint=self._input_fingerprint,
            events=deepcopy(self._events),
            provider_calls=deepcopy(self._provider_calls),
            final_status=final_status,
            generated_at=self._clock(),
        )


def fingerprint(value: str) -> str:
    """Return a stable non-reversible SHA-256 fingerprint."""

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def digest_mapping(value: dict[str, Any]) -> str:
    """Return a stable digest for arguments without recording their values."""

    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
