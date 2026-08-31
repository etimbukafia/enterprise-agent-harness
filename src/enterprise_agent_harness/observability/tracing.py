"""Structured trace recording for runtime decisions."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from copy import deepcopy
from datetime import datetime
from threading import RLock
from typing import Any, Protocol

from ..contracts import ExecutionContext, OutcomeStatus, PolicyDecision, ToolExecutionRecord
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
        initial_trace: RunTrace | None = None,
    ) -> None:
        self.execution = execution
        self.sink = sink
        self._id = id_factory
        self._clock = clock
        self._input_fingerprint = fingerprint(input_text)
        if initial_trace is not None and (
            initial_trace.execution_id != execution.execution_id
            or initial_trace.agent_id != execution.agent_id
            or initial_trace.agent_version != execution.agent_version
            or initial_trace.principal_id != execution.principal.principal_id
            or initial_trace.tenant_id != execution.principal.tenant_id
            or initial_trace.session_id != execution.principal.session_id
            or initial_trace.input_fingerprint != self._input_fingerprint
            or initial_trace.correlation_id != execution.correlation_id
            or initial_trace.parent_execution_id != execution.parent_execution_id
            or initial_trace.delegation_id != execution.delegation_id
            or initial_trace.delegation_depth != execution.delegation_depth
            or initial_trace.delegation_path != execution.delegation_path
        ):
            raise ValueError("initial trace does not match the execution")
        self._events: list[TraceEvent] = (
            deepcopy(initial_trace.events) if initial_trace is not None else []
        )
        self._provider_calls: list[ProviderCallRecord] = (
            deepcopy(initial_trace.provider_calls) if initial_trace is not None else []
        )
        self._policy_decisions: list[PolicyDecision] = (
            deepcopy(initial_trace.policy_decisions) if initial_trace is not None else []
        )
        self._tool_executions: list[ToolExecutionRecord] = (
            deepcopy(initial_trace.tool_executions) if initial_trace is not None else []
        )

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

    def record_policy_decision(self, decision: PolicyDecision) -> None:
        """Record one explicit policy decision without raw action data."""

        self._policy_decisions.append(decision.model_copy(deep=True))

    def record_tool_execution(self, record: ToolExecutionRecord) -> None:
        """Record one registry execution summary without raw arguments."""

        self._tool_executions.append(record.model_copy(deep=True))

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
            correlation_id=self.execution.correlation_id,
            parent_execution_id=self.execution.parent_execution_id,
            delegation_id=self.execution.delegation_id,
            delegation_depth=self.execution.delegation_depth,
            delegation_path=self.execution.delegation_path,
            events=deepcopy(self._events),
            provider_calls=deepcopy(self._provider_calls),
            policy_decisions=deepcopy(self._policy_decisions),
            tool_executions=deepcopy(self._tool_executions),
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
