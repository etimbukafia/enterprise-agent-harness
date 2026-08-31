"""Small shared fixtures for the Phase 15 examples.

The fixtures use the same typed tools, policies, registries, and factory as an
application. They are local examples, not production integrations.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from enterprise_agent_harness import (
    AgentConfig,
    AgentFactory,
    AgentLifecycleStatus,
    AgentRegistry,
    AgentTemplate,
    AgentVersion,
    CapabilityDefinition,
    CapabilityRegistry,
    DeterministicProvider,
    InMemoryApprovalBroker,
    InMemoryStateStore,
    ListAuditSink,
    ListTraceSink,
    PolicyDefinition,
    PolicyEffect,
    PolicyRule,
    PrincipalContext,
    ProviderProfile,
    RiskLevel,
    ToolDefinition,
    ToolKind,
    ToolRegistry,
    VersionReference,
)


class TextInput(BaseModel):
    """Input used by the small example tools."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)


class TextOutput(BaseModel):
    """Output used by the small example tools."""

    model_config = ConfigDict(extra="forbid")

    value: str


def principal(
    principal_id: str = "developer",
    tenant_id: str = "example-tenant",
) -> PrincipalContext:
    """Return one local example identity."""

    return PrincipalContext(
        principal_id=principal_id,
        tenant_id=tenant_id,
        session_id=f"session-{principal_id}",
    )


def read_tool(
    tool_id: str = "records-read",
    *,
    handler: Callable[[Any, TextInput], TextOutput] | None = None,
) -> ToolDefinition:
    """Create one safe read tool."""

    default_handler = lambda _context, arguments: TextOutput(value=f"read: {arguments.query}")
    return ToolDefinition(
        tool_id=tool_id,
        version="1.0.0",
        description="Read one record for an example request.",
        input_model=TextInput,
        output_model=TextOutput,
        handler=handler or default_handler,
        kind=ToolKind.READ,
        risk_level=RiskLevel.LOW,
        owner_id="example-team",
        tags=("example", "read"),
    )


def write_tool(
    tool_id: str = "records-write",
    *,
    handler: Callable[[Any, TextInput], TextOutput] | None = None,
) -> ToolDefinition:
    """Create an idempotent write tool."""

    default_handler = lambda _context, arguments: TextOutput(value=f"write: {arguments.query}")
    return ToolDefinition(
        tool_id=tool_id,
        version="1.0.0",
        description="Write one record for an example request.",
        input_model=TextInput,
        output_model=TextOutput,
        handler=handler or default_handler,
        kind=ToolKind.WRITE,
        risk_level=RiskLevel.MEDIUM,
        required_permissions=("records:write",),
        idempotency_required=True,
        owner_id="example-team",
        tags=("example", "write"),
    )


def action_tool(
    tool_id: str = "records-publish",
    *,
    handler: Callable[[Any, TextInput], TextOutput] | None = None,
) -> ToolDefinition:
    """Create an action tool that requires exact human approval."""

    default_handler = lambda _context, arguments: TextOutput(value=f"publish: {arguments.query}")
    return ToolDefinition(
        tool_id=tool_id,
        version="1.0.0",
        description="Publish one record after human approval.",
        input_model=TextInput,
        output_model=TextOutput,
        handler=handler or default_handler,
        kind=ToolKind.ACTION,
        risk_level=RiskLevel.HIGH,
        required_permissions=("records:publish",),
        requires_approval=True,
        idempotency_required=True,
        owner_id="example-team",
        tags=("example", "action"),
    )


def capability(
    tool_id: str,
    capability_id: str = "records-review",
    *,
    risk_level: RiskLevel = RiskLevel.LOW,
) -> CapabilityDefinition:
    """Create one registry capability for a tool."""

    return CapabilityDefinition(
        capability_id=capability_id,
        version="1.0.0",
        description="Use the example record operation.",
        supported_operations=["read" if risk_level == RiskLevel.LOW else "act"],
        supported_intents=["review_records"],
        supported_languages=["en"],
        allowed_tool_ids=[tool_id],
        risk_level=risk_level,
        owner_id="example-team",
        lifecycle=AgentLifecycleStatus.ACTIVE,
    )


def allow_policy(
    tool_id: str,
    policy_id: str = "records-policy",
) -> PolicyDefinition:
    """Create a deny-by-default policy with one explicit allow rule."""

    return PolicyDefinition(
        policy_id=policy_id,
        version="1.0.0",
        description="Allow the selected example tool.",
        default_effect=PolicyEffect.DENY,
        rules=[
            PolicyRule(
                rule_id=f"allow-{tool_id}",
                effect=PolicyEffect.ALLOW,
                tool_ids=[tool_id],
            )
        ],
        owner_id="example-team",
        lifecycle=AgentLifecycleStatus.ACTIVE,
    )


def make_factory(
    tools: Sequence[ToolDefinition],
    *,
    capabilities: Sequence[CapabilityDefinition] = (),
    policies: Sequence[PolicyDefinition] = (),
    approval_broker: InMemoryApprovalBroker | None = None,
    provider: Any | None = None,
) -> tuple[AgentFactory, ToolRegistry, CapabilityRegistry, ListTraceSink, ListAuditSink]:
    """Build the local registries and factory used by the examples."""

    tool_registry = ToolRegistry(tools)
    capability_registry = CapabilityRegistry(tools=tool_registry)
    for item in capabilities:
        capability_registry.register(item)
    agent_registry = AgentRegistry(
        capabilities=capability_registry,
        tools=tool_registry,
        policies=policies,
    )
    traces = ListTraceSink()
    audits = ListAuditSink()
    selected_provider = provider or DeterministicProvider(
        tool_id=tools[0].tool_id if tools else None
    )
    factory = AgentFactory(
        agent_registry=agent_registry,
        providers={("deterministic", "1.0.0"): selected_provider},
        default_state_store=InMemoryStateStore(),
        approval_broker=approval_broker,
        trace_sink=traces,
        audit_sink=audits,
    )
    return factory, tool_registry, capability_registry, traces, audits


def agent_config(
    agent_id: str,
    *,
    tool_id: str | None,
    template: AgentTemplate | None = AgentTemplate.READ_ONLY_ANALYST,
    risk_level: RiskLevel = RiskLevel.LOW,
    capability_ids: Sequence[str] = (),
    policy_ids: Sequence[str] = (),
    version: str = "1.0.0",
) -> AgentConfig:
    """Create a small declarative configuration for one example agent."""

    return AgentConfig(
        identity=AgentVersion(agent_id=agent_id, version=version),
        goal=f"Run the {agent_id} example safely.",
        supported_intents=["review_records"],
        supported_languages=["en"],
        capabilities=[
            VersionReference(component_id=item, version="1.0.0") for item in capability_ids
        ],
        allowed_tools=(
            [VersionReference(component_id=tool_id, version="1.0.0")] if tool_id is not None else []
        ),
        policies=[VersionReference(component_id=item, version="1.0.0") for item in policy_ids],
        provider_profile=ProviderProfile(
            provider_id="deterministic",
            version="1.0.0",
            model="example-model",
        ),
        risk_level=risk_level,
        approval_requirements=[tool_id]
        if template == AgentTemplate.APPROVAL_GATED_OPERATOR
        else [],
        owner_id="example-team",
        template=template,
    )


__all__ = [
    "TextInput",
    "TextOutput",
    "action_tool",
    "agent_config",
    "allow_policy",
    "capability",
    "make_factory",
    "principal",
    "read_tool",
    "write_tool",
]
