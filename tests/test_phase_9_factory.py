"""Acceptance tests for declarative Phase 9 agent construction."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from enterprise_agent_harness import (
    AgentConfig,
    AgentFactory,
    AgentLifecycleStatus,
    AgentRegistry,
    AgentTemplate,
    AgentVersion,
    BoundedMemory,
    ComponentReference,
    ComponentType,
    DeterministicProvider,
    FactoryAuthorizationError,
    FactoryDependencyError,
    FactoryTemplateError,
    InMemoryStateStore,
    ListAuditSink,
    ListTraceSink,
    OutcomeStatus,
    PolicyDefinition,
    PolicyEffect,
    PolicyRule,
    PrincipalContext,
    PromptDefinition,
    PromptRegistry,
    ProviderProfile,
    RegistryError,
    RiskLevel,
    RuntimeConfig,
    RuntimeProfile,
    RuntimeProfileReference,
    SkillDefinition,
    SkillRegistry,
    ToolDefinition,
    ToolKind,
    ToolRegistry,
)


class RecordInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = Field(min_length=1)


class RecordOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str


def read_tool() -> ToolDefinition:
    return ToolDefinition(
        tool_id="records-read",
        version="1.0.0",
        description="Read one record.",
        input_model=RecordInput,
        output_model=RecordOutput,
        handler=lambda _context, arguments: RecordOutput(value=arguments.value),
        kind=ToolKind.READ,
        risk_level=RiskLevel.LOW,
        owner_id="records-team",
    )


def active_skill() -> SkillDefinition:
    return SkillDefinition(
        skill_id="record-review",
        version="1.0.0",
        name="Record review",
        description="Review records.",
        supported_operations=("read",),
        supported_intents=("review_records",),
        supported_languages=("en",),
        required_tool_refs=(
            ComponentReference(
                component_type=ComponentType.TOOL,
                component_id="records-read",
                version="1.0.0",
            ),
        ),
        risk_level=RiskLevel.LOW,
        owner_id="records-team",
        lifecycle=AgentLifecycleStatus.ACTIVE,
    )


def active_policy() -> PolicyDefinition:
    return PolicyDefinition(
        policy_id="records-policy",
        version="1.0.0",
        description="Allow record reads.",
        default_effect=PolicyEffect.DENY,
        rules=[
            PolicyRule(
                rule_id="allow-record-read",
                effect=PolicyEffect.ALLOW,
                tool_ids=["records-read"],
            )
        ],
        lifecycle=AgentLifecycleStatus.ACTIVE,
    )


def make_factory() -> tuple[AgentFactory, AgentRegistry, ListTraceSink]:
    tools = ToolRegistry([read_tool()])
    skills = SkillRegistry(tools=tools)
    skills.register(active_skill())
    prompts = PromptRegistry(
        [
            PromptDefinition(
                prompt_id="records-prompt",
                version="1.0.0",
                purpose="Review records safely.",
                instructions="Use the configured record review skill.",
            )
        ]
    )
    registry = AgentRegistry(
        prompts=prompts,
        skills=skills,
        tools=tools,
        policies=[active_policy()],
    )
    traces = ListTraceSink()
    factory = AgentFactory(
        agent_registry=registry,
        providers={("deterministic", "1.0.0"): DeterministicProvider(tool_id="records-read")},
        runtime_profiles={
            ("bounded-read", "1.0.0"): RuntimeProfile(
                profile_id="bounded-read",
                version="1.0.0",
                description="One-step read-only runtime.",
                runtime_limits=RuntimeConfig(max_plan_steps=1),
            )
        },
        memory_strategies={"bounded": BoundedMemory()},
        state_stores={"in-memory": InMemoryStateStore()},
        trace_sink=traces,
        audit_sink=ListAuditSink(),
    )
    return factory, registry, traces


def config(agent_id: str = "records-agent") -> AgentConfig:
    return AgentConfig(
        identity=AgentVersion(agent_id=agent_id, version="1.0.0"),
        goal="Review records and report approved findings.",
        supported_intents=["review_records"],
        supported_languages=["en"],
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
        runtime_profile=RuntimeProfileReference(profile_id="bounded-read", version="1.0.0"),
        state_strategy="in-memory",
        memory_strategy="bounded",
        owner_id="platform-team",
        template=AgentTemplate.READ_ONLY_ANALYST,
    )


def principal() -> Any:
    return PrincipalContext(
        principal_id="factory-owner",
        tenant_id="tenant-factory",
        session_id="session-factory",
    )


def test_factory_resolves_exact_components_registers_and_runs_agent() -> None:
    factory, registry, _traces = make_factory()

    built = factory.build(config())

    assert built.registered is True
    assert built.dry_run is False
    assert built.runtime is not None
    assert built.manifest.schema_version == "agent-resolved-manifest.v2"
    assert built.manifest.manifest_digest
    assert built.manifest.runtime_profile is not None
    assert built.manifest.runtime_limits.max_plan_steps == 1
    assert [reference.component_id for reference in built.manifest.tool_refs] == ["records-read"]
    assert [reference.component_id for reference in built.manifest.skill_refs] == ["record-review"]
    assert [reference.component_id for reference in built.manifest.policy_refs] == [
        "records-policy"
    ]
    assert built.manifest.prompt_id == "records-prompt"
    assert built.manifest.registry_snapshot_id
    assert registry.resolve("records-agent", "1.0.0").lifecycle == AgentLifecycleStatus.ACTIVE
    assert factory.validate(config()).manifest_digest == built.manifest.manifest_digest

    outcome = built.execute(principal(), "review record-1")

    assert outcome.status == OutcomeStatus.COMPLETED
    assert outcome.agent_id == "records-agent"
    assert outcome.agent_version == "1.0.0"
    assert len(outcome.tool_calls) == 1

    registry.suspend("records-agent", "1.0.0")
    with pytest.raises(FactoryAuthorizationError, match="not active"):
        built.execute(principal(), "review record-2")


def test_factory_dry_run_validates_without_registering_or_constructing_runtime() -> None:
    factory, registry, _traces = make_factory()

    dry_run = factory.build(config("dry-run-agent"), dry_run=True)

    assert dry_run.dry_run is True
    assert dry_run.registered is False
    assert dry_run.runtime is None
    with pytest.raises(FactoryDependencyError, match="runtime is not built"):
        factory.runtime_for("dry-run-agent", "1.0.0")
    with pytest.raises(RegistryError, match="unknown agent"):
        registry.get("dry-run-agent", "1.0.0")
    with pytest.raises(ValidationError):
        dry_run.manifest.manifest_id = "mutated"


def test_factory_rejects_missing_dependencies_and_invalid_templates() -> None:
    factory, _registry, _traces = make_factory()

    missing_tool = config("missing-tool-agent").model_copy(
        update={
            "tool_refs": [
                ComponentReference(
                    component_type=ComponentType.TOOL,
                    component_id="missing-tool",
                    version="1.0.0",
                )
            ]
        }
    )
    with pytest.raises(FactoryDependencyError, match="tool is unavailable"):
        factory.validate(missing_tool)

    invalid_action = config("invalid-action-agent").model_copy(
        update={"template": AgentTemplate.ACTION_AGENT}
    )
    with pytest.raises(FactoryTemplateError, match="requires a write or action tool"):
        factory.validate(invalid_action)


def test_standard_template_helper_preserves_declarative_input() -> None:
    values: dict[str, Any] = {
        "agent_id": "helper-agent",
        "version": "1.0.0",
        "goal": "Route requests.",
        "prompt_ref": {
            "component_type": "prompt",
            "component_id": "records-prompt",
            "version": "1.0.0",
        },
        "provider_profile": {
            "provider_id": "deterministic",
            "version": "1.0.0",
            "model": "test-model",
        },
    }

    result = AgentFactory.template_config(AgentTemplate.ROUTER, **values)

    assert result.identity == AgentVersion(agent_id="helper-agent", version="1.0.0")
    assert result.template == AgentTemplate.ROUTER
