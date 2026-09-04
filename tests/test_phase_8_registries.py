"""Acceptance tests for versioned agent, prompt, and skill registries."""

from __future__ import annotations

from dataclasses import replace

import pytest
from pydantic import BaseModel, ConfigDict, Field

from enterprise_agent_harness import (
    AgentDefinition,
    AgentLifecycleStatus,
    AgentRegistry,
    AgentVersion,
    ComponentReference,
    ComponentType,
    DuplicateRegistrationError,
    IncompatibleRegistrationError,
    ListRegistryAuditSink,
    PolicyDefinition,
    PolicyEffect,
    PolicyRule,
    PromptDefinition,
    PromptRegistry,
    ProviderProfile,
    RegistryError,
    RiskLevel,
    SkillDefinition,
    SkillRegistry,
    StaleRegistrationError,
    ToolDefinition,
    ToolKind,
    ToolRegistry,
)


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = Field(min_length=1)


class Output(BaseModel):
    value: str


def read_tool() -> ToolDefinition:
    return ToolDefinition(
        tool_id="records-read",
        version="1.0.0",
        description="Read one record.",
        input_model=Input,
        output_model=Output,
        handler=lambda _context, arguments: Output(value=arguments.value),
        kind=ToolKind.READ,
        risk_level=RiskLevel.LOW,
        owner_id="records-team",
    )


def skill(*, lifecycle: AgentLifecycleStatus = AgentLifecycleStatus.ACTIVE):
    return SkillDefinition(
        skill_id="record-review",
        version="1.0.0",
        name="Record review",
        description="Review records.",
        supported_operations=("read",),
        supported_intents=("review_records",),
        supported_languages=("en", "fr"),
        required_tool_refs=(
            ComponentReference(
                component_type=ComponentType.TOOL,
                component_id="records-read",
                version="1.0.0",
            ),
        ),
        risk_level=RiskLevel.LOW,
        owner_id="records-team",
        lifecycle=lifecycle,
        metadata={"p95_ms": 120, "quality": "verified"},
    )


def active_policy() -> PolicyDefinition:
    return PolicyDefinition(
        policy_id="records-policy",
        version="1.0.0",
        description="Allow record reads.",
        default_effect=PolicyEffect.DENY,
        lifecycle=AgentLifecycleStatus.ACTIVE,
        rules=[
            PolicyRule(
                rule_id="allow-record-read",
                effect=PolicyEffect.ALLOW,
                tool_ids=["records-read"],
            )
        ],
    )


def agent(*, agent_id: str = "records-agent") -> AgentDefinition:
    return AgentDefinition(
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
        risk_level=RiskLevel.LOW,
        owner_id="platform-team",
        lifecycle=AgentLifecycleStatus.DRAFT,
        performance_metadata={"target_latency_ms": 500, "quality_gate": "verified"},
    )


def test_registries_validate_lifecycle_search_and_read_only_queries() -> None:
    sink = ListRegistryAuditSink()
    tools = ToolRegistry([read_tool()])
    prompts = PromptRegistry(
        [
            PromptDefinition(
                prompt_id="records-prompt",
                version="1.0.0",
                purpose="Review records.",
                instructions="Use the record review skill.",
            )
        ],
        audit_sink=sink,
    )
    skills = SkillRegistry(tools=tools, audit_sink=sink)
    skills.register(skill())
    registry = AgentRegistry(
        prompts=prompts,
        skills=skills,
        tools=tools,
        policies=[active_policy()],
        audit_sink=sink,
    )

    registered = registry.register(agent())
    assert registered.lifecycle == AgentLifecycleStatus.DRAFT
    validated = registry.validate("records-agent", "1.0.0")
    active = registry.activate("records-agent", "1.0.0")
    assert validated.lifecycle == AgentLifecycleStatus.VALIDATED
    assert active.lifecycle == AgentLifecycleStatus.ACTIVE
    assert registry.resolve("records-agent", "1.0.0") == active

    found = registry.search(intent="review_records", language="en")
    assert [item.agent_id for item in found] == ["records-agent"]
    found[0].supported_intents.append("untrusted-change")
    assert registry.get("records-agent", "1.0.0").supported_intents == ["review_records"]

    skill_results = skills.search(
        intent="review_records",
        language="fr",
        tool_id="records-read",
    )
    assert [item.skill_id for item in skill_results] == ["record-review"]
    assert skill_results[0].metadata["p95_ms"] == 120

    suspended = registry.suspend("records-agent", "1.0.0")
    assert suspended.lifecycle == AgentLifecycleStatus.SUSPENDED
    with pytest.raises(RegistryError, match="not active"):
        registry.resolve("records-agent", "1.0.0")
    registry.activate("records-agent", "1.0.0")
    deprecated = registry.deprecate("records-agent", "1.0.0")
    retired = registry.retire("records-agent", "1.0.0")
    assert deprecated.lifecycle == AgentLifecycleStatus.DEPRECATED
    assert retired.lifecycle == AgentLifecycleStatus.RETIRED
    operations = [event.operation for event in registry.events]
    assert operations[:4] == ["registered", "validated", "activated", "suspended"]
    assert operations[-2:] == ["deprecated", "retired"]

    assert skills.suspend("record-review", "1.0.0").lifecycle == (AgentLifecycleStatus.SUSPENDED)
    assert skills.activate("record-review", "1.0.0").lifecycle == (AgentLifecycleStatus.ACTIVE)
    assert skills.deprecate("record-review", "1.0.0").lifecycle == (AgentLifecycleStatus.DEPRECATED)
    assert skills.retire("record-review", "1.0.0").lifecycle == (AgentLifecycleStatus.RETIRED)


def test_registry_rejects_duplicates_stale_versions_and_incompatible_dependencies() -> None:
    tools = ToolRegistry([read_tool()])
    prompts = PromptRegistry(
        [
            PromptDefinition(
                prompt_id="records-prompt",
                version="1.0.0",
                purpose="Review records.",
                instructions="Use the record review skill.",
            )
        ]
    )
    skills = SkillRegistry(tools=tools)
    registered_skill = skills.register(skill())
    with pytest.raises(DuplicateRegistrationError, match="unique"):
        skills.register(registered_skill)
    with pytest.raises(StaleRegistrationError, match="immutable"):
        skills.register(registered_skill, replace_existing=True)

    registry = AgentRegistry(
        prompts=prompts,
        skills=skills,
        tools=tools,
        policies=[active_policy()],
    )
    registered_agent = registry.register(agent())
    with pytest.raises(DuplicateRegistrationError, match="unique"):
        registry.register(registered_agent)
    with pytest.raises(StaleRegistrationError, match="immutable"):
        registry.register(registered_agent, replace_existing=True)

    broken = agent(agent_id="broken-agent").model_copy(
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
    registry.register(broken)
    with pytest.raises(IncompatibleRegistrationError, match="unknown tool"):
        registry.activate("broken-agent", "1.0.0")

    tools.register(replace(read_tool(), tool_id="records-high", risk_level=RiskLevel.HIGH))
    risky = agent(agent_id="risky-agent").model_copy(
        update={
            "tool_refs": [
                ComponentReference(
                    component_type=ComponentType.TOOL,
                    component_id="records-high",
                    version="1.0.0",
                )
            ]
        }
    )
    registry.register(risky)
    with pytest.raises(IncompatibleRegistrationError, match="risk"):
        registry.activate("risky-agent", "1.0.0")


def test_registry_snapshot_is_versioned_and_contains_exact_dependency_graph() -> None:
    tools = ToolRegistry(
        [
            read_tool(),
            replace(
                read_tool(),
                tool_id="records-read-wrapper",
                dependencies=(
                    ComponentReference(
                        component_type=ComponentType.TOOL,
                        component_id="records-read",
                        version="1.0.0",
                    ),
                ),
            ),
        ]
    )
    prompts = PromptRegistry(
        [
            PromptDefinition(
                prompt_id="records-prompt",
                version="1.0.0",
                purpose="Review records.",
                instructions="Use the record review skill.",
            )
        ]
    )
    skills = SkillRegistry(tools=tools)
    skills.register(skill())
    registry = AgentRegistry(
        prompts=prompts,
        skills=skills,
        tools=tools,
        policies=[active_policy()],
    )
    registry.register(agent())
    registry.activate("records-agent", "1.0.0")

    snapshot = registry.snapshot()
    restored = type(snapshot).model_validate_json(snapshot.model_dump_json())
    relations = {
        (edge.source_kind, edge.target_kind, edge.relation) for edge in snapshot.dependencies
    }

    assert restored == snapshot
    assert snapshot.schema_version == "agent-registry-snapshot.v2"
    assert snapshot.revision > 0
    assert [item.agent_id for item in snapshot.agents] == ["records-agent"]
    assert [item.skill_id for item in snapshot.skills] == ["record-review"]
    assert ("agent", "prompt", "uses_prompt") in relations
    assert ("agent", "skill", "uses_skill") in relations
    assert ("agent", "tool", "allows_tool") in relations
    assert ("agent", "policy", "uses_policy") in relations
    assert ("skill", "tool", "requires_tool") in relations
    skill_snapshot = skills.snapshot()
    skill_relations = {
        (edge.source_kind, edge.target_kind, edge.relation) for edge in skill_snapshot.dependencies
    }
    assert ("tool", "tool", "depends_on") in skill_relations
    assert any(event.operation == "snapshot_created" for event in registry.events)
    assert any(event.operation == "snapshot_created" for event in registry.audit_sink.events)
