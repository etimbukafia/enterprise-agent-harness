# Quickstart

This guide uses the real factory and runtime API. It uses the deterministic
provider, so it does not need an API key.

## Install

Use Python 3.11 or later.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

## Build and run an agent

Create `quickstart.py` with this code:

```python
from pydantic import BaseModel, ConfigDict, Field

from enterprise_agent_harness import (
    AgentConfig,
    AgentFactory,
    AgentLifecycleStatus,
    AgentRegistry,
    AgentTemplate,
    AgentVersion,
    DeterministicProvider,
    ListAuditSink,
    ListTraceSink,
    OutcomeStatus,
    PolicyDefinition,
    PolicyEffect,
    PolicyRule,
    PrincipalContext,
    ProviderProfile,
    ToolDefinition,
    ToolKind,
    ToolRegistry,
    VersionReference,
)


class Query(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str


tool = ToolDefinition(
    tool_id="records-read",
    version="1.0.0",
    description="Read one record.",
    input_model=Query,
    output_model=Answer,
    handler=lambda _context, arguments: Answer(value=f"read: {arguments.query}"),
    kind=ToolKind.READ,
    owner_id="example-team",
)
tools = ToolRegistry([tool])
policy = PolicyDefinition(
    policy_id="records-policy",
    version="1.0.0",
    description="Allow record reads.",
    default_effect=PolicyEffect.DENY,
    rules=[
        PolicyRule(
            rule_id="allow-record-read",
            effect=PolicyEffect.ALLOW,
            tool_ids=[tool.tool_id],
        )
    ],
    lifecycle=AgentLifecycleStatus.ACTIVE,
)
registry = AgentRegistry(tools=tools, policies=[policy])
factory = AgentFactory(
    agent_registry=registry,
    providers={
        ("deterministic", "1.0.0"): DeterministicProvider(tool_id=tool.tool_id)
    },
    trace_sink=ListTraceSink(),
    audit_sink=ListAuditSink(),
)
config = AgentConfig(
    identity=AgentVersion(agent_id="records-analyst", version="1.0.0"),
    goal="Review records.",
    allowed_tools=[VersionReference(component_id=tool.tool_id, version="1.0.0")],
    policies=[VersionReference(component_id=policy.policy_id, version="1.0.0")],
    provider_profile=ProviderProfile(
        provider_id="deterministic",
        version="1.0.0",
        model="example-model",
    ),
    owner_id="example-team",
    template=AgentTemplate.READ_ONLY_ANALYST,
)
agent = factory.build(config)

principal = PrincipalContext(
    principal_id="developer",
    tenant_id="example-tenant",
    session_id="quickstart-session",
)
outcome = agent.execute(principal, "Review record-42", execution_id="quickstart-1")
trace = agent.trace_for(outcome.execution_id)
print(outcome.status.value)
print(trace.final_status.value if trace.final_status else "no final status")
```

Run it:

```powershell
python quickstart.py
```

The factory resolves exact tool and policy versions. The runtime checks the
tool argument, permission, and policy before it calls the handler. The outcome
is runtime evidence. The trace contains safe execution evidence.

The same path is available as a repository example:

```powershell
python -m examples.quickstart
```

The example modules use the deterministic provider and local in-memory stores.
Replace these components for a production deployment. See
`production-extension-points.md`.
