"""Router to specialist composition example.

Run from the repository root with:

    python -m examples.composition_delegation
"""

from __future__ import annotations

from enterprise_agent_harness import (
    AgentComposer,
    AgentTemplate,
    CompositionDefinition,
    CompositionPattern,
    CompositionStep,
    ExecutionContext,
    OutcomeStatus,
)

from ._support import agent_config, allow_policy, make_factory, principal, read_tool, skill


def main() -> None:
    """Select one specialist and delegate under the parent authority."""

    tool = read_tool()
    factory, _tools, _skills, _traces, _audits = make_factory(
        [tool],
        skills=[skill(tool.tool_id)],
        policies=[allow_policy(tool.tool_id)],
    )
    factory.build(
        agent_config(
            "router-agent",
            tool_id=None,
            template=AgentTemplate.ROUTER,
        )
    )
    for specialist_id in ("records-specialist", "audit-specialist"):
        factory.build(
            agent_config(
                specialist_id,
                tool_id=tool.tool_id,
                skill_ids=("records-review",),
                policy_ids=("records-policy",),
            )
        )

    owner = principal()
    parent = ExecutionContext(
        execution_id="router-execution",
        agent_id="router-agent",
        agent_version="1.0.0",
        principal=owner,
        authorized_tool_ids=(tool.tool_id,),
        authorized_tool_versions=(f"{tool.tool_id}@1.0.0",),
        max_steps=2,
        state_id="router-state",
        max_risk_level=tool.risk_level,
        correlation_id="composition-correlation",
    )
    definition = CompositionDefinition(
        composition_id="record-router",
        version="1.0.0",
        pattern=CompositionPattern.ROUTER,
        steps=[
            CompositionStep(
                step_id="records",
                agent_id="records-specialist",
                agent_version="1.0.0",
            ),
            CompositionStep(
                step_id="audit",
                agent_id="audit-specialist",
                agent_version="1.0.0",
            ),
        ],
    )
    selected_step = "records" if "record" in "Review record-42".lower() else "audit"
    result = AgentComposer(factory).compose(
        parent,
        definition,
        "Review record-42",
        selected_step_id=selected_step,
    )
    assert len(result.outcomes) == 1
    assert result.final_outcome.status == OutcomeStatus.COMPLETED
    print(f"selected specialist: {selected_step}")
    print(f"final status: {result.final_outcome.status.value}")
    print(f"child execution: {result.outcomes[0].execution_id}")


if __name__ == "__main__":
    main()
