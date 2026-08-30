"""Typed contracts shared by the enterprise agent runtime.

These contracts are provider-neutral. Provider output is proposal data. The
runtime creates the trusted execution context and decides the final outcome.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal, Self

from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utc_now() -> datetime:
    """Return an aware UTC timestamp."""

    return datetime.now(UTC)


def _validate_component_version(value: str) -> str:
    """Validate and normalize a three-segment PEP 440 component version."""

    try:
        parsed = Version(value)
    except InvalidVersion as exc:
        raise ValueError("version must use a valid PEP 440 version") from exc
    if len(parsed.release) != 3:
        raise ValueError("version must contain MAJOR.MINOR.PATCH")
    return str(parsed)


class ContractModel(BaseModel):
    """Base model for public runtime contracts."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ToolKind(str, Enum):
    """Operational kind of a tool."""

    READ = "read"
    WRITE = "write"
    ACTION = "action"


class RiskLevel(str, Enum):
    """Risk level declared by the application for a tool or capability."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ToolResultStatus(str, Enum):
    """Normalized result state for one tool invocation."""

    SUCCEEDED = "succeeded"
    EMPTY = "empty"
    RESTRICTED = "restricted"
    FAILED = "failed"
    INVALID_ARGUMENTS = "invalid_arguments"
    PERMISSION_DENIED = "permission_denied"
    APPROVAL_REQUIRED = "approval_required"


class OutcomeStatus(str, Enum):
    """Final state of one agent execution."""

    COMPLETED = "completed"
    PARTIAL = "partial"
    NEEDS_INPUT = "needs_input"
    REFUSED = "refused"
    ESCALATED = "escalated"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class SafetyFlag(str, Enum):
    """Deterministic safety signal attached to an execution outcome."""

    DIRECT_PROMPT_INJECTION = "direct_prompt_injection"
    INDIRECT_PROMPT_INJECTION = "indirect_prompt_injection"
    RESTRICTED_RESULT = "restricted_result"
    PERMISSION_DENIED = "permission_denied"
    APPROVAL_REQUIRED = "approval_required"
    PLAN_VALIDATION_FAILED = "plan_validation_failed"
    TOOL_VALIDATION_FAILED = "tool_validation_failed"
    TOOL_FAILURE = "tool_failure"
    TOOL_TIMEOUT = "tool_timeout"
    PROVIDER_FAILURE = "provider_failure"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_OUTPUT_INVALID = "provider_output_invalid"
    NO_RESULT = "no_result"
    CONFLICTING_RESULT = "conflicting_result"
    LOW_CONFIDENCE = "low_confidence"
    VERIFICATION_FAILED = "verification_failed"
    EXECUTION_TIMEOUT = "execution_timeout"
    EXECUTION_CANCELLED = "execution_cancelled"
    RETRY_BUDGET_EXHAUSTED = "retry_budget_exhausted"


class ContextTrust(str, Enum):
    """Trust label for one provider context block."""

    TRUSTED = "trusted"
    UNTRUSTED = "untrusted"


class ContextBlockType(str, Enum):
    """Role of one block in compiled provider context."""

    POLICY = "policy"
    PRINCIPAL = "principal"
    EXECUTION = "execution"
    CAPABILITY = "capability"
    STATE = "state"
    MEMORY = "memory"
    INPUT = "input"
    TOOL_OUTPUT = "tool_output"


class RecoveryAction(str, Enum):
    """Safe next action after an execution outcome."""

    NONE = "none"
    REQUEST_INPUT = "request_input"
    REFUSE = "refuse"
    ESCALATE = "escalate"
    RETRY = "retry"
    ABORT = "abort"


class MemoryScope(str, Enum):
    """Scope of an optional memory item."""

    EXECUTION = "execution"
    PRINCIPAL = "principal"
    TENANT = "tenant"


class ExecutionStateStatus(str, Enum):
    """Lifecycle state stored for a workflow execution."""

    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    REFUSED = "refused"
    ESCALATED = "escalated"


class AgentLifecycleStatus(str, Enum):
    """Lifecycle state for a versioned agent or registry component."""

    DRAFT = "draft"
    VALIDATED = "validated"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DEPRECATED = "deprecated"
    RETIRED = "retired"


class PolicyEffect(str, Enum):
    """Effect of one declarative policy rule."""

    ALLOW = "allow"
    DENY = "deny"


class PrincipalContext(ContractModel):
    """Trusted identity supplied by the consuming application."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True, frozen=True)

    principal_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)


class ExecutionContext(ContractModel):
    """Trusted authority and correlation context for one execution."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True, frozen=True)

    execution_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    agent_version: str = Field(min_length=1)
    principal: PrincipalContext
    authorized_tool_ids: tuple[str, ...] = ()
    granted_permissions: tuple[str, ...] = ()
    approved_action_digests: tuple[str, ...] = ()
    max_steps: int = Field(default=3, ge=1, le=100)
    state_id: str = Field(min_length=1)
    environment: str = Field(default="development", min_length=1)
    max_risk_level: RiskLevel = RiskLevel.CRITICAL

    @model_validator(mode="after")
    def authority_lists_are_unique(self) -> Self:
        if len(self.authorized_tool_ids) != len(set(self.authorized_tool_ids)):
            raise ValueError("authorized_tool_ids must not contain duplicates")
        if len(self.granted_permissions) != len(set(self.granted_permissions)):
            raise ValueError("granted_permissions must not contain duplicates")
        if len(self.approved_action_digests) != len(set(self.approved_action_digests)):
            raise ValueError("approved_action_digests must not contain duplicates")
        return self


class MemoryItem(ContractModel):
    """Small optional memory value. Memory is not authority or policy."""

    memory_id: str = Field(min_length=1)
    principal_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    scope: MemoryScope
    source_scope_id: str = Field(min_length=1)
    key: str = Field(min_length=1)
    value: str = Field(min_length=1)
    origin: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def timestamp_is_aware(self) -> Self:
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at must include timezone information")
        return self


class ExecutionState(ContractModel):
    """Versioned workflow state that is separate from optional memory."""

    schema_version: str = "agent-state.v1"
    state_id: str = Field(min_length=1)
    execution_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    agent_version: str = Field(min_length=1)
    principal_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    status: ExecutionStateStatus = ExecutionStateStatus.PENDING
    version: int = Field(default=0, ge=0)
    data: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def timestamp_is_aware(self) -> Self:
        if self.updated_at.tzinfo is None or self.updated_at.utcoffset() is None:
            raise ValueError("updated_at must include timezone information")
        return self


class ContextBlock(ContractModel):
    """One bounded block sent to a provider."""

    block_id: str = Field(min_length=1)
    block_type: ContextBlockType
    trust: ContextTrust
    source: str = Field(min_length=1)
    content: str = Field(min_length=1)
    priority: int = Field(ge=0, le=100)
    token_estimate: int = Field(ge=1)


class CompiledContext(ContractModel):
    """Bounded provider context with explicit trust labels."""

    schema_version: str = "agent-context.v1"
    execution_id: str = Field(min_length=1)
    principal_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    input_text: str = Field(min_length=1)
    blocks: list[ContextBlock] = Field(min_length=1)
    dropped_block_ids: list[str] = Field(default_factory=list)
    character_count: int = Field(ge=1)

    @model_validator(mode="after")
    def context_blocks_are_valid(self) -> Self:
        block_ids = [block.block_id for block in self.blocks]
        if len(block_ids) != len(set(block_ids)):
            raise ValueError("context block IDs must be unique")
        required = {ContextBlockType.POLICY, ContextBlockType.PRINCIPAL, ContextBlockType.INPUT}
        present = {block.block_type for block in self.blocks}
        if not required.issubset(present):
            raise ValueError("compiled context requires policy, principal, and input blocks")
        return self

    def render(self) -> str:
        """Render blocks without removing their trust labels."""

        return "\n\n".join(
            f"[{block.block_type.value}|{block.trust.value}|{block.source}]\n{block.content}"
            for block in self.blocks
        )

    @property
    def trusted_blocks(self) -> tuple[ContextBlock, ...]:
        """Return the trusted partition of the compiled context."""

        return tuple(block for block in self.blocks if block.trust == ContextTrust.TRUSTED)

    @property
    def untrusted_blocks(self) -> tuple[ContextBlock, ...]:
        """Return the untrusted partition of the compiled context."""

        return tuple(block for block in self.blocks if block.trust == ContextTrust.UNTRUSTED)

    def render_trusted(self) -> str:
        """Render only trusted runtime and application blocks."""

        return _render_context_blocks(self.trusted_blocks)

    def render_untrusted(self) -> str:
        """Render only caller, memory, and tool-output blocks."""

        return _render_context_blocks(self.untrusted_blocks)


def _render_context_blocks(blocks: tuple[ContextBlock, ...]) -> str:
    """Render a context partition with its trust labels intact."""

    return "\n\n".join(
        f"[{block.block_type.value}|{block.trust.value}|{block.source}]\n{block.content}"
        for block in blocks
    )


class CapabilityDefinition(ContractModel):
    """Configured capability exposed to a provider and runtime."""

    capability_id: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_-]*$")
    version: str = Field(min_length=1)
    description: str = Field(min_length=1)
    supported_operations: list[str] = Field(min_length=1)
    allowed_tool_ids: list[str] = Field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.LOW
    owner_id: str = Field(default="application", min_length=1)
    lifecycle: AgentLifecycleStatus = AgentLifecycleStatus.ACTIVE
    tags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def lists_are_unique(self) -> Self:
        for name in ("supported_operations", "allowed_tool_ids", "tags"):
            values = getattr(self, name)
            if len(values) != len(set(values)):
                raise ValueError(f"{name} must not contain duplicates")
        return self


class ToolDescriptor(ContractModel):
    """Provider-facing tool metadata without a handler or authority."""

    tool_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    description: str = Field(min_length=1)
    kind: ToolKind
    risk_level: RiskLevel
    input_fields: list[str] = Field(default_factory=list)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    required_permissions: list[str] = Field(default_factory=list)
    requires_approval: bool = False
    idempotency_required: bool = False
    owner_id: str = Field(default="application", min_length=1)
    tags: list[str] = Field(default_factory=list)
    lifecycle: AgentLifecycleStatus = AgentLifecycleStatus.ACTIVE
    timeout_seconds: float | None = Field(default=None, gt=0.0)
    retryable: bool = False
    max_attempts: int = Field(default=1, ge=1, le=10)
    retry_backoff_seconds: float = Field(default=0.0, ge=0.0, le=60.0)
    dependencies: list[str] = Field(default_factory=list)
    allowed_environments: list[str] = Field(default_factory=list)


class EvidenceRef(ContractModel):
    """A reference to data returned by a tool."""

    evidence_id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    kind: str = Field(default="tool_output", min_length=1)


class ToolResult(ContractModel):
    """Typed, untrusted result envelope returned by one tool."""

    tool_id: str = Field(min_length=1)
    tool_version: str | None = None
    execution_id: str | None = None
    status: ToolResultStatus
    output: Any | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    restricted: bool = False
    conflicts: list[str] = Field(default_factory=list)
    injection_flags: list[str] = Field(default_factory=list)
    error_code: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def result_flags_are_consistent(self) -> Self:
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("tool result evidence IDs must be unique")
        if self.restricted and self.evidence:
            raise ValueError("a restricted result cannot contain evidence")
        if self.status == ToolResultStatus.RESTRICTED and self.evidence:
            raise ValueError("a restricted result cannot contain evidence")
        return self


class ResourceContext(ContractModel):
    """Optional resource facts supplied by the consuming application."""

    resource_type: str = Field(min_length=1)
    resource_id: str | None = Field(default=None, min_length=1)
    attributes: dict[str, Any] = Field(default_factory=dict)


class PolicyDecision(ContractModel):
    """Explicit deterministic result of one policy evaluation."""

    schema_version: Literal["agent-policy-decision.v1"] = "agent-policy-decision.v1"
    decision_id: str = Field(default="policy_decision", min_length=1)
    allowed: bool
    principal_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    tool_id: str = Field(min_length=1)
    environment: str = Field(min_length=1)
    risk_level: RiskLevel
    reason_code: str = Field(min_length=1)
    policy_id: str | None = Field(default=None, min_length=1)
    policy_version: str | None = Field(default=None, min_length=1)
    rule_id: str | None = Field(default=None, min_length=1)
    matched_rule_ids: list[str] = Field(default_factory=list)
    approval_required: bool = False
    resource_type: str | None = Field(default=None, min_length=1)
    resource_id: str | None = Field(default=None, min_length=1)
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def matched_rule_ids_are_unique(self) -> Self:
        if len(self.matched_rule_ids) != len(set(self.matched_rule_ids)):
            raise ValueError("matched_rule_ids must not contain duplicates")
        return self


class PermissionDecision(ContractModel):
    """Application decision for one proposed tool call."""

    allowed: bool
    principal_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    tool_id: str = Field(min_length=1)
    reason_code: str = Field(min_length=1)
    approval_required: bool = False
    agent_id: str | None = Field(default=None, min_length=1)
    environment: str | None = Field(default=None, min_length=1)
    risk_level: RiskLevel | None = None
    policy_decision: PolicyDecision | None = None


class ApprovalDecision(ContractModel):
    """Exact approval evidence for one sensitive action proposal."""

    approval_id: str = Field(min_length=1)
    action_digest: str = Field(min_length=1)
    approved: bool
    decided_by: str = Field(min_length=1)
    reason_code: str = Field(min_length=1)
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def expiry_is_aware(self) -> Self:
        if self.expires_at is not None and (
            self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None
        ):
            raise ValueError("expires_at must include timezone information")
        return self


class ToolCall(ContractModel):
    """Canonical provider-neutral proposal for one tool call."""

    call_id: str | None = Field(default=None, min_length=1)
    tool_id: str = Field(min_length=1)
    tool_version: str | None = Field(default=None, min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    purpose: str = Field(min_length=1)
    idempotency_key: str | None = Field(default=None, min_length=1)


class ActionProposal(ContractModel):
    """Provider-neutral proposal for a potentially side-effecting action."""

    schema_version: Literal["agent-action-proposal.v1"] = "agent-action-proposal.v1"
    action_id: str = Field(min_length=1)
    execution_id: str = Field(min_length=1)
    tool_call: ToolCall
    risk_level: RiskLevel
    requires_approval: bool = False
    justification: str = Field(min_length=1)

    @property
    def proposal_id(self) -> str:
        """Return the stable action identifier."""

        return self.action_id


class ApprovalRequest(ContractModel):
    """Exact action request sent to an application approval boundary."""

    schema_version: Literal["agent-approval-request.v1"] = "agent-approval-request.v1"
    request_id: str = Field(min_length=1)
    execution_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    agent_version: str = Field(min_length=1)
    principal_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    action: ActionProposal
    action_digest: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def expiry_is_aware(self) -> Self:
        if self.expires_at is not None and (
            self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None
        ):
            raise ValueError("expires_at must include timezone information")
        return self


class VersionReference(ContractModel):
    """Exact reference to a versioned registry component."""

    component_id: str = Field(min_length=1)
    version: str = Field(min_length=1)

    @field_validator("version")
    @classmethod
    def version_is_pep440_component_version(cls, value: str) -> str:
        return _validate_component_version(value)


class PolicyRule(ContractModel):
    """Typed, domain-neutral rule input for a policy evaluator."""

    rule_id: str = Field(min_length=1)
    effect: PolicyEffect
    tool_ids: list[str] = Field(default_factory=list)
    agent_ids: list[str] = Field(default_factory=list)
    principal_ids: list[str] = Field(default_factory=list)
    tenant_ids: list[str] = Field(default_factory=list)
    required_permissions: list[str] = Field(default_factory=list)
    environments: list[str] = Field(default_factory=list)
    risk_levels: list[RiskLevel] = Field(default_factory=list)
    resource_types: list[str] = Field(default_factory=list)
    resource_ids: list[str] = Field(default_factory=list)
    requires_approval: bool | None = None

    @model_validator(mode="after")
    def lists_are_unique(self) -> Self:
        for name in (
            "tool_ids",
            "agent_ids",
            "principal_ids",
            "tenant_ids",
            "required_permissions",
            "environments",
            "risk_levels",
            "resource_types",
            "resource_ids",
        ):
            values = getattr(self, name)
            if len(values) != len(set(values)):
                raise ValueError(f"{name} must not contain duplicates")
        return self


class PolicyDefinition(ContractModel):
    """Versioned declarative policy metadata owned by the application."""

    schema_version: Literal["agent-policy.v1"] = "agent-policy.v1"
    policy_id: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_-]*$")
    version: str = Field(min_length=1)
    description: str = Field(min_length=1)
    owner_id: str = Field(default="application", min_length=1)
    default_effect: PolicyEffect = PolicyEffect.DENY
    rules: list[PolicyRule] = Field(default_factory=list)
    lifecycle: AgentLifecycleStatus = AgentLifecycleStatus.DRAFT

    @field_validator("version")
    @classmethod
    def version_is_pep440_component_version(cls, value: str) -> str:
        return _validate_component_version(value)

    @model_validator(mode="after")
    def rule_ids_are_unique(self) -> Self:
        rule_ids = [rule.rule_id for rule in self.rules]
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("policy rule IDs must be unique")
        return self


class PlanStep(ContractModel):
    """One provider-proposed tool call."""

    step_id: str = Field(min_length=1)
    tool_id: str = Field(min_length=1)
    tool_version: str | None = None
    purpose: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    required: bool = True
    idempotency_key: str | None = None


class AgentPlan(ContractModel):
    """Provider plan before runtime validation."""

    steps: list[PlanStep] = Field(default_factory=list)
    stop_reason: str | None = None

    @model_validator(mode="after")
    def step_ids_are_unique(self) -> Self:
        step_ids = [step.step_id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("plan step IDs must be unique")
        return self


class OutcomeProposal(ContractModel):
    """Provider proposal for an outcome. The runtime chooses the final state."""

    summary: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(default_factory=list)
    output: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def evidence_ids_are_unique(self) -> Self:
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("evidence_ids must not contain duplicates")
        return self


class ToolCallRecord(ContractModel):
    """Structured record of one proposed and executed tool call."""

    call_id: str = Field(min_length=1)
    step_id: str = Field(min_length=1)
    tool_id: str = Field(min_length=1)
    tool_version: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    result_status: ToolResultStatus
    evidence_ids: list[str] = Field(default_factory=list)
    latency_ms: float = Field(default=0.0, ge=0.0)
    retry_count: int = Field(default=0, ge=0)
    permission_reason_code: str | None = None


class ToolExecutionRecord(ContractModel):
    """Structured record for one registry-managed handler invocation."""

    schema_version: Literal["agent-tool-execution.v1"] = "agent-tool-execution.v1"
    execution_id: str = Field(min_length=1)
    tool_id: str = Field(min_length=1)
    tool_version: str = Field(min_length=1)
    status: ToolResultStatus
    attempts: int = Field(default=1, ge=1)
    retry_count: int = Field(default=0, ge=0)
    latency_ms: float = Field(default=0.0, ge=0.0)
    timeout_seconds: float | None = Field(default=None, gt=0.0)
    idempotency_key_digest: str | None = Field(default=None, min_length=1)
    error_code: str | None = Field(default=None, min_length=1)


class VerificationResult(ContractModel):
    """Deterministic verification of provider output against tool results."""

    supported: bool
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_coverage: float = Field(ge=0.0, le=1.0)
    invalid_evidence_ids: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


class AgentOutcome(ContractModel):
    """Final runtime-owned result for one agent execution."""

    schema_version: str = "agent-outcome.v1"
    outcome_id: str = Field(min_length=1)
    execution_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    agent_version: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    principal_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    status: OutcomeStatus
    summary: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)
    safety_flags: list[SafetyFlag] = Field(default_factory=list)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    verification: VerificationResult | None = None
    recovery: RecoveryAction = RecoveryAction.NONE
    human_review_required: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    error_code: str | None = None
    trace_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def timestamp_is_aware(self) -> Self:
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at must include timezone information")
        return self


class RuntimeConfig(ContractModel):
    """Deterministic runtime limits and safety thresholds."""

    max_plan_steps: int = Field(default=3, ge=1, le=100)
    max_context_characters: int = Field(default=12000, ge=256, le=100000)
    high_confidence_threshold: float = Field(default=0.80, ge=0.0, le=1.0)
    minimum_confidence_threshold: float = Field(default=0.60, ge=0.0, le=1.0)
    provider_timeout_seconds: float = Field(default=30.0, gt=0.0, le=600.0)
    provider_max_attempts: int = Field(default=1, ge=1, le=10)
    provider_retry_backoff_seconds: float = Field(default=0.0, ge=0.0, le=60.0)
    execution_timeout_seconds: float | None = Field(default=60.0, gt=0.0, le=3600.0)
    max_retries: int = Field(default=3, ge=0, le=100)
    environment: str = Field(default="development", min_length=1)
    max_risk_level: RiskLevel = RiskLevel.CRITICAL


class AgentVersion(ContractModel):
    """Immutable identity for one versioned agent definition."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True, frozen=True)

    agent_id: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_-]*$")
    version: str = Field(min_length=1)

    @field_validator("version")
    @classmethod
    def version_is_pep440_component_version(cls, value: str) -> str:
        return _validate_component_version(value)

    @property
    def identity(self) -> str:
        """Return the stable `agent_id@version` identity."""

        return f"{self.agent_id}@{self.version}"


class ProviderProfile(ContractModel):
    """Versioned provider configuration reference in an agent definition."""

    provider_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    model: str = Field(min_length=1)
    options: dict[str, Any] = Field(default_factory=dict)

    @field_validator("version")
    @classmethod
    def version_is_pep440_component_version(cls, value: str) -> str:
        return _validate_component_version(value)


class AgentDefinition(ContractModel):
    """Declarative, versioned description of an enterprise agent."""

    schema_version: Literal["agent-definition.v1"] = "agent-definition.v1"
    identity: AgentVersion
    goal: str = Field(min_length=1)
    capabilities: list[VersionReference] = Field(default_factory=list)
    allowed_tools: list[VersionReference] = Field(default_factory=list)
    policies: list[VersionReference] = Field(default_factory=list)
    provider_profile: ProviderProfile
    runtime_limits: RuntimeConfig = Field(default_factory=RuntimeConfig)
    risk_level: RiskLevel = RiskLevel.LOW
    approval_requirements: list[str] = Field(default_factory=list)
    state_strategy: str | None = Field(default=None, min_length=1)
    memory_strategy: str | None = Field(default=None, min_length=1)
    owner_id: str = Field(default="application", min_length=1)
    lifecycle: AgentLifecycleStatus = AgentLifecycleStatus.DRAFT

    @model_validator(mode="before")
    @classmethod
    def accept_flat_identity_input(cls, value: object) -> object:
        """Accept flat identity fields while storing one nested identity."""

        if not isinstance(value, Mapping) or "identity" in value:
            return value
        has_agent_id = "agent_id" in value
        has_version = "version" in value
        if not has_agent_id and not has_version:
            return value
        if not has_agent_id or not has_version:
            raise ValueError("agent_id and version must be provided together")
        normalized = dict(value)
        normalized["identity"] = {
            "agent_id": normalized.pop("agent_id"),
            "version": normalized.pop("version"),
        }
        return normalized

    @model_validator(mode="after")
    def references_are_unique(self) -> Self:
        for name in ("capabilities", "allowed_tools", "policies", "approval_requirements"):
            values = getattr(self, name)
            if name == "approval_requirements":
                keys = values
            else:
                keys = [(item.component_id, item.version) for item in values]
            if len(keys) != len(set(keys)):
                raise ValueError(f"{name} must not contain duplicates")
        return self

    @property
    def agent_id(self) -> str:
        """Return the logical agent identity."""

        return self.identity.agent_id

    @property
    def version(self) -> str:
        """Return the immutable agent version."""

        return self.identity.version
