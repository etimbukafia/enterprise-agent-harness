"""Agent Factory and registry query examples.

Run from the repository root with:

    python -m examples.factory_and_registry
"""

from __future__ import annotations

from enterprise_agent_harness import AgentTemplate

from ._support import agent_config, allow_policy, make_factory, read_tool, skill


def main() -> None:
    """Query existing functionality, then build one exact agent version."""

    tool = read_tool()
    factory, tools, skills, _traces, _audits = make_factory(
        [tool],
        skills=[skill(tool.tool_id)],
        policies=[allow_policy(tool.tool_id)],
    )
    config = agent_config(
        "factory-analyst",
        tool_id=tool.tool_id,
        template=AgentTemplate.READ_ONLY_ANALYST,
        skill_ids=("records-review",),
        policy_ids=("records-policy",),
    )

    print(
        "existing agents:",
        [item.agent_id for item in factory.agent_registry.search(intent="review_records")],
    )
    print(
        "existing skills:",
        [item.skill_id for item in skills.search(tool_id=tool.tool_id)],
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
