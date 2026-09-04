"""Provider-neutral request, response, and call-metadata contracts."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import Field

from ..contracts import (
    AgentPlan,
    CompiledContext,
    ContractModel,
    ExecutionContext,
    OutcomeProposal,
    PromptDefinition,
    SkillDefinition,
    ToolDescriptor,
    ToolResult,
)


class ProviderOperation(str, Enum):
    """Type marker for a provider operation."""

    INTERPRET = "interpret"
    PLAN = "plan"
    COMPOSE = "compose"


class ProviderCallMetadata(ContractModel):
    """Provider and usage metadata safe to record in a trace."""

    schema_version: Literal["agent-provider-call.v1"] = "agent-provider-call.v1"
    provider_id: str = Field(default="unknown", min_length=1)
    provider_version: str = Field(default="unknown", min_length=1)
    model: str = Field(default="unknown", min_length=1)
    request_id: str | None = Field(default=None, min_length=1)
    latency_ms: float = Field(default=0.0, ge=0.0)
    retry_count: int = Field(default=0, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    metadata: dict[str, str] = Field(default_factory=dict)


class ProviderCallRecord(ContractModel):
    """One provider operation recorded for trace export."""

    schema_version: Literal["agent-provider-call-record.v1"] = "agent-provider-call-record.v1"
    operation: ProviderOperation
    metadata: ProviderCallMetadata


class InterpretationProposal(ContractModel):
    """Provider proposal that describes an agent request without authority."""

    intent: str = Field(min_length=1)
    parameters: dict[str, Any] = Field(default_factory=dict)
    constraints: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class InterpretationRequest(ContractModel):
    """Provider input for generic request interpretation."""

    schema_version: Literal["agent-interpretation-request.v2"] = "agent-interpretation-request.v2"
    request_id: str = Field(min_length=1)
    context: CompiledContext
    execution: ExecutionContext
    prompt: PromptDefinition | None = None
    skills: list[SkillDefinition] = Field(default_factory=list)
    tools: list[ToolDescriptor] = Field(default_factory=list)


class InterpretationResponse(ContractModel):
    """Validated provider response for request interpretation."""

    schema_version: Literal["agent-interpretation-response.v1"] = "agent-interpretation-response.v1"
    proposal: InterpretationProposal
    metadata: ProviderCallMetadata = Field(default_factory=ProviderCallMetadata)


class PlanningRequest(ContractModel):
    """Provider input for a bounded tool plan."""

    schema_version: Literal["agent-planning-request.v2"] = "agent-planning-request.v2"
    request_id: str = Field(min_length=1)
    context: CompiledContext
    execution: ExecutionContext
    prompt: PromptDefinition | None = None
    skills: list[SkillDefinition] = Field(default_factory=list)
    tools: list[ToolDescriptor] = Field(default_factory=list)
    interpretation: InterpretationResponse | None = None


class PlanningResponse(ContractModel):
    """Validated provider response containing a canonical bounded plan."""

    schema_version: Literal["agent-planning-response.v1"] = "agent-planning-response.v1"
    plan: AgentPlan
    metadata: ProviderCallMetadata = Field(default_factory=ProviderCallMetadata)


class CompositionRequest(ContractModel):
    """Provider input for outcome composition after tool execution."""

    schema_version: Literal["agent-composition-request.v1"] = "agent-composition-request.v1"
    request_id: str = Field(min_length=1)
    context: CompiledContext
    execution: ExecutionContext
    plan: AgentPlan
    tool_results: list[ToolResult] = Field(default_factory=list)


class CompositionResponse(ContractModel):
    """Validated provider response containing an outcome proposal."""

    schema_version: Literal["agent-composition-response.v1"] = "agent-composition-response.v1"
    proposal: OutcomeProposal
    metadata: ProviderCallMetadata = Field(default_factory=ProviderCallMetadata)


__all__ = [
    "CompositionRequest",
    "CompositionResponse",
    "InterpretationProposal",
    "InterpretationRequest",
    "InterpretationResponse",
    "PlanningRequest",
    "PlanningResponse",
    "ProviderCallMetadata",
    "ProviderCallRecord",
    "ProviderOperation",
]
