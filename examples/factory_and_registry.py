"""Agent Factory and registry query examples.

Run from the repository root with:

    python -m examples.factory_and_registry
"""

from __future__ import annotations

from enterprise_agent_harness import AgentFactory, AgentTemplate, AgentVersion, ProviderProfile

from ._support import allow_policy, capability, make_factory, read_tool


def main() -> None:
    """Query existing functionality, then build one exact agent version."""

    tool = read_tool()
    factory, tools, capabilities, _traces, _audits = make_factory(
        [tool],
        capabilities=[capability(tool.tool_id)],
        policies=[allow_policy(tool.tool_id)],
    )
    config = AgentFactory.template_config(
        AgentTemplate.READ_ONLY_ANALYST,
        identity=AgentVersion(agent_id="factory-analyst", version="1.0.0"),
        goal="Review records.",
        supported_intents=["review_records"],
        supported_languages=["en"],
        capabilities=[{"component_id": "records-review", "version": "1.0.0"}],
        allowed_tools=[{"component_id": tool.tool_id, "version": "1.0.0"}],
        policies=[{"component_id": "records-policy", "version": "1.0.0"}],
        provider_profile=ProviderProfile(
            provider_id="deterministic",
            version="1.0.0",
            model="example-model",
        ),
        owner_id="example-team",
    )

    print(
        "existing agents:",
        [item.agent_id for item in factory.agent_registry.search(intent="review_records")],
    )
    print(
        "existing capabilities:",
        [item.capability_id for item in capabilities.search(tool_id=tool.tool_id)],
    )
    print("existing tool versions:", tools.versions(tool.tool_id))

    manifest = factory.validate(config)
    built = factory.build(config)
    print(f"validated manifest: {manifest.manifest_id}")
    print(f"built manifest digest: {built.manifest.manifest_digest}")
    print(
        "agents after build:",
        [item.agent_id for item in factory.agent_registry.list()],
    )


if __name__ == "__main__":
    main()
