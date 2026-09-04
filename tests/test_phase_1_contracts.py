from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from enterprise_agent_harness import (
    ActionProposal,
    AgentDefinition,
    AgentLifecycleStatus,
    AgentOutcome,
    AgentVersion,
    ApprovalDecision,
    ApprovalRequest,
    ComponentReference,
    ComponentType,
    ExecutionContext,
    OutcomeStatus,
    PolicyDefinition,
    PolicyEffect,
    PolicyRule,
    PrincipalContext,
    ProviderProfile,
    RiskLevel,
    RuntimeConfig,
    SkillDefinition,
    ToolCall,
    ToolDefinition,
    ToolKind,
)
from enterprise_agent_harness.errors import (
    ErrorCode,
    PolicyDeniedError,
    ProviderTimeoutError,
)


class ContractInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = Field(min_length=1)


class ContractOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accepted: bool


def test_agent_definition_round_trips_and_agent_version_is_immutable() -> None:
    definition = AgentDefinition(
        identity=AgentVersion(agent_id="records", version="1.2.3"),
        goal="Review and update approved records.",
        prompt_ref=ComponentReference(
            component_type=ComponentType.PROMPT,
            component_id="records-prompt",
            version="1.0.0",
        ),
        skill_refs=[
            ComponentReference(
                component_type=ComponentType.SKILL,
                component_id="record-review",
                version="1.0.0",
            )
        ],
        tool_refs=[
            ComponentReference(
                component_type=ComponentType.TOOL,
                component_id="records-read",
                version="1.0.0",
            )
        ],
        policy_refs=[
            ComponentReference(
                component_type=ComponentType.POLICY,
                component_id="records-policy",
                version="1.0.0",
            )
        ],
        provider_profile=ProviderProfile(
            provider_id="deterministic",
            version="1.0.0",
            model="test-model",
        ),
        runtime_limits=RuntimeConfig(max_plan_steps=4),
        risk_level=RiskLevel.MEDIUM,
        approval_requirements=["record.write"],
        state_strategy="in_memory",
        memory_strategy="bounded",
        owner_id="platform",
        lifecycle=AgentLifecycleStatus.VALIDATED,
    )

    restored = AgentDefinition.model_validate_json(definition.model_dump_json())

    assert restored == definition
    assert definition.agent_id == "records"
    assert definition.version == "1.2.3"
    assert definition.identity.identity == "records@1.2.3"
    with pytest.raises((ValidationError, TypeError)):
        definition.identity.version = "2.0.0"

    with pytest.raises(ValidationError, match="MAJOR.MINOR.PATCH"):
        AgentVersion(agent_id="records", version="1")

    flat_definition = AgentDefinition(
        agent_id="records",
        version="1.2.3",
        goal="Review and update approved records.",
        prompt_ref=ComponentReference(
            component_type=ComponentType.PROMPT,
            component_id="records-prompt",
            version="1.0.0",
        ),
        provider_profile=ProviderProfile(
            provider_id="deterministic",
            version="1.0.0",
            model="test-model",
        ),
    )
    assert flat_definition.identity == definition.identity


def test_policy_skill_and_tool_contracts_round_trip_with_typed_metadata() -> None:
    policy = PolicyDefinition(
        policy_id="records-policy",
        version="1.0.0",
        description="Allow approved record reads.",
        owner_id="governance",
        default_effect=PolicyEffect.DENY,
        rules=[
            PolicyRule(
                rule_id="allow-read",
                effect=PolicyEffect.ALLOW,
                tool_ids=["records-read"],
                required_permissions=["records:read"],
                risk_levels=[RiskLevel.LOW],
            )
        ],
    )
    skill = SkillDefinition(
        skill_id="record-review",
        version="1.0.0",
        name="Record review",
        description="Review records.",
        supported_operations=("read",),
        required_tool_refs=(
            ComponentReference(
                component_type=ComponentType.TOOL,
                component_id="records-read",
                version="1.0.0",
            ),
        ),
        owner_id="records-team",
    )
    tool = ToolDefinition(
        tool_id="records-read",
        version="1.0.0",
        description="Read one record.",
        input_model=ContractInput,
        output_model=ContractOutput,
        handler=lambda _context, _arguments: ContractOutput(accepted=True),
        kind=ToolKind.READ,
        owner_id="records-team",
        tags=("records", "read"),
    )

    restored_policy = PolicyDefinition.model_validate_json(policy.model_dump_json())
    restored_skill = SkillDefinition.model_validate_json(skill.model_dump_json())
    descriptor = tool.descriptor
    restored_descriptor = type(descriptor).model_validate_json(descriptor.model_dump_json())

    assert restored_policy == policy
    assert restored_skill == skill
    assert restored_descriptor == descriptor
    assert descriptor.input_schema["properties"]["value"]["type"] == "string"
    assert descriptor.output_schema["properties"]["accepted"]["type"] == "boolean"


def test_action_and_approval_contracts_round_trip_and_keep_exact_digest() -> None:
    principal = PrincipalContext(
        principal_id="person-1",
        tenant_id="tenant-1",
        session_id="session-1",
    )
    execution = ExecutionContext(
        execution_id="execution-1",
        agent_id="records",
        agent_version="1.0.0",
        principal=principal,
        authorized_tool_ids=("records-write",),
        state_id="state-1",
    )
    call = ToolCall(
        call_id="call-1",
        tool_id="records-write",
        tool_version="1.0.0",
        arguments={"value": "approved"},
        purpose="Apply the approved record update.",
        idempotency_key="idempotency-1",
    )
    action = ActionProposal(
        action_id="action-1",
        execution_id=execution.execution_id,
        tool_call=call,
        risk_level=RiskLevel.HIGH,
        requires_approval=True,
        justification="The caller approved this change.",
    )
    request = ApprovalRequest(
        request_id="request-1",
        execution_id=execution.execution_id,
        agent_id=execution.agent_id,
        agent_version=execution.agent_version,
        principal_id=principal.principal_id,
        tenant_id=principal.tenant_id,
        action=action,
        action_digest="digest-1",
        reason="Review the exact update.",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    decision = ApprovalDecision(
        approval_id="approval-1",
        request_id=request.request_id,
        action_digest=request.action_digest,
        approved=True,
        decided_by="reviewer-1",
        reason_code="approved",
        expires_at=request.expires_at,
    )

    assert ApprovalRequest.model_validate_json(request.model_dump_json()) == request
    assert ApprovalDecision.model_validate_json(decision.model_dump_json()) == decision
    assert request.action.proposal_id == "action-1"
    assert decision.action_digest == request.action_digest


def test_execution_and_outcome_contracts_round_trip_with_standard_states() -> None:
    owner = PrincipalContext(
        principal_id="person-1",
        tenant_id="tenant-1",
        session_id="session-1",
    )
    execution = ExecutionContext(
        execution_id="execution-1",
        agent_id="records",
        agent_version="1.0.0",
        principal=owner,
        authorized_tool_ids=("records-read",),
        granted_permissions=("records:read",),
        state_id="state-1",
    )
    outcome = AgentOutcome(
        outcome_id="outcome-1",
        execution_id=execution.execution_id,
        agent_id=execution.agent_id,
        agent_version=execution.agent_version,
        session_id=owner.session_id,
        principal_id=owner.principal_id,
        tenant_id=owner.tenant_id,
        status=OutcomeStatus.NEEDS_INPUT,
        summary="More information is required.",
    )

    assert ExecutionContext.model_validate_json(execution.model_dump_json()) == execution
    assert AgentOutcome.model_validate_json(outcome.model_dump_json()) == outcome
    assert {status.value for status in OutcomeStatus} == {
        "completed",
        "partial",
        "needs_input",
        "refused",
        "escalated",
        "failed",
        "timed_out",
        "cancelled",
    }


def test_core_contracts_reject_duplicate_policy_rules_deterministically() -> None:
    with pytest.raises(ValidationError, match="rule IDs must be unique"):
        PolicyDefinition(
            policy_id="records-policy",
            version="1.0.0",
            description="Reject duplicate rules.",
            rules=[
                PolicyRule(rule_id="same", effect=PolicyEffect.DENY),
                PolicyRule(rule_id="same", effect=PolicyEffect.ALLOW),
            ],
        )


def test_error_taxonomy_exposes_stable_codes_and_retry_hints() -> None:
    denied = PolicyDeniedError()
    timed_out = ProviderTimeoutError()

    assert denied.code == ErrorCode.POLICY_DENIED
    assert denied.retryable is False
    assert timed_out.code == ErrorCode.PROVIDER_TIMEOUT
    assert timed_out.retryable is True
