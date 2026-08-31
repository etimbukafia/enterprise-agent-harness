"""Stable contracts for trace export and external evaluation."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Protocol, Self, TypeVar

from pydantic import Field, JsonValue, model_validator

from ..contracts import (
    ContractModel,
    ExecutionMetrics,
    OutcomeStatus,
    PolicyDecision,
    PrincipalContext,
    ResolvedAgentManifest,
    ResourceContext,
    ToolExecutionRecord,
)
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
    event_id: str | None = Field(default=None, min_length=1)
    trigger_id: str | None = Field(default=None, min_length=1)
    causation_id: str | None = Field(default=None, min_length=1)
    attempt: int = Field(default=0, ge=0)
    events: list[TraceEvent] = Field(default_factory=list)
    provider_calls: list[ProviderCallRecord] = Field(default_factory=list)
    policy_decisions: list[PolicyDecision] = Field(default_factory=list)
    tool_executions: list[ToolExecutionRecord] = Field(default_factory=list)
    metrics: ExecutionMetrics | None = None
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


class EvaluationExecutionInput(ContractModel):
    """One validated interactive execution input from an external test case."""

    schema_version: Literal["agent-evaluation-input.v1"] = "agent-evaluation-input.v1"
    case_id: str = Field(min_length=1)
    principal: PrincipalContext
    input_text: str = Field(min_length=1)
    execution_id: str = Field(min_length=1)
    correlation_id: str | None = Field(default=None, min_length=1)
    resource: ResourceContext | None = None


TestCaseT_contra = TypeVar("TestCaseT_contra", contravariant=True)


class TestCaseAdapter(Protocol[TestCaseT_contra]):
    """Convert one external test case into a valid harness execution input."""

    def adapt(self, test_case: TestCaseT_contra) -> EvaluationExecutionInput:
        """Return the harness input for the external test case."""


class EvaluationSubject(ContractModel):
    """Stable identity for one baseline or candidate agent build."""

    schema_version: Literal["agent-evaluation-subject.v1"] = "agent-evaluation-subject.v1"
    role: Literal["baseline", "candidate"]
    agent_id: str = Field(min_length=1)
    agent_version: str = Field(min_length=1)
    manifest_id: str = Field(min_length=1)
    manifest_digest: str = Field(min_length=1)

    @classmethod
    def from_manifest(
        cls,
        manifest: ResolvedAgentManifest,
        *,
        role: Literal["baseline", "candidate"],
    ) -> EvaluationSubject:
        """Create an evaluation identity from an immutable resolved manifest."""

        return cls(
            role=role,
            agent_id=manifest.agent.agent_id,
            agent_version=manifest.agent.version,
            manifest_id=manifest.manifest_id,
            manifest_digest=manifest.manifest_digest,
        )


class EvaluationEvidence(ContractModel):
    """JSON-safe evidence that an external evaluator can consume."""

    schema_version: Literal["agent-evaluation-evidence.v1"] = "agent-evaluation-evidence.v1"
    case_id: str = Field(min_length=1)
    subject: EvaluationSubject
    run_trace: dict[str, JsonValue]
    manifest: dict[str, JsonValue]


class MetricHook(Protocol):
    """External policy that derives numeric metrics from exported evidence."""

    def __call__(self, evidence: EvaluationEvidence) -> dict[str, float]:
        """Return externally defined metrics for one evaluation evidence bundle."""


class HardGateHook(Protocol):
    """External policy that returns a pass or fail result for exported evidence."""

    def __call__(self, evidence: EvaluationEvidence) -> bool:
        """Return the external hard-gate result for one evidence bundle."""


class RecordedReplay(ContractModel):
    """Offline reconstruction of exported evidence without live execution."""

    schema_version: Literal["agent-recorded-replay.v1"] = "agent-recorded-replay.v1"
    source_trace_id: str = Field(min_length=1)
    source_execution_id: str = Field(min_length=1)
    live_actions_executed: Literal[False] = False
    trace: RunTrace
