"""Stable contracts for trace export and deterministic replay."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Self

from pydantic import Field, model_validator

from ..contracts import ContractModel, OutcomeStatus, PolicyDecision, ToolExecutionRecord
from ..providers.contracts import ProviderCallRecord


class TraceEvent(ContractModel):
    """One structured runtime event with safe metadata only."""

    schema_version: Literal["agent-trace-event.v1"] = "agent-trace-event.v1"
    event_id: str = Field(min_length=1)
    execution_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    stage: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    occurred_at: datetime
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def timestamp_is_aware(self) -> Self:
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must include timezone information")
        return self


class RunTrace(ContractModel):
    """Exportable trace bundle for replay or external evaluation."""

    schema_version: Literal["agent-run-trace.v1"] = "agent-run-trace.v1"
    trace_id: str = Field(min_length=1)
    execution_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    agent_version: str = Field(min_length=1)
    principal_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    input_fingerprint: str = Field(min_length=1)
    correlation_id: str = Field(default="root", min_length=1)
    parent_execution_id: str | None = Field(default=None, min_length=1)
    delegation_id: str | None = Field(default=None, min_length=1)
    delegation_depth: int = Field(default=0, ge=0, le=100)
    delegation_path: tuple[str, ...] = ()
    events: list[TraceEvent] = Field(default_factory=list)
    provider_calls: list[ProviderCallRecord] = Field(default_factory=list)
    policy_decisions: list[PolicyDecision] = Field(default_factory=list)
    tool_executions: list[ToolExecutionRecord] = Field(default_factory=list)
    final_status: OutcomeStatus | None = None
    generated_at: datetime

    @model_validator(mode="after")
    def events_are_ordered_and_timestamp_is_aware(self) -> Self:
        sequences = [event.sequence for event in self.events]
        if sequences != list(range(1, len(sequences) + 1)):
            raise ValueError("trace event sequences must start at one and be contiguous")
        if self.generated_at.tzinfo is None or self.generated_at.utcoffset() is None:
            raise ValueError("generated_at must include timezone information")
        if self.parent_execution_id is None and self.delegation_depth != 0:
            raise ValueError("root trace cannot have a delegation depth")
        if self.parent_execution_id is not None and self.delegation_depth < 1:
            raise ValueError("delegated trace must have a positive delegation depth")
        if len(self.delegation_path) != len(set(self.delegation_path)):
            raise ValueError("delegation_path must not contain duplicates")
        return self


class ReplayRequest(ContractModel):
    """Input contract for a caller-controlled deterministic replay."""

    schema_version: Literal["agent-replay.v1"] = "agent-replay.v1"
    trace_id: str = Field(min_length=1)
    execution_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    agent_version: str = Field(min_length=1)
    input_fingerprint: str = Field(min_length=1)
    state_id: str = Field(min_length=1)
    state_version: int = Field(ge=0)
    seed: int = 0
    tool_result_fixtures: dict[str, Any] = Field(default_factory=dict)
