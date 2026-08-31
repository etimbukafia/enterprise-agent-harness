"""Minimal read-only analyst example.

Run from the repository root with:

    python -m examples.read_only_analyst
"""

from __future__ import annotations

import json

from enterprise_agent_harness import OutcomeStatus

from ._support import agent_config, allow_policy, capability, make_factory, principal, read_tool


def build_agent():
    """Build an analyst with one typed read tool and one allow policy."""

    tool = read_tool()
    factory, _tools, _capabilities, _traces, _audits = make_factory(
        [tool],
        capabilities=[capability(tool.tool_id)],
        policies=[allow_policy(tool.tool_id)],
    )
    config = agent_config(
        "records-analyst",
        tool_id=tool.tool_id,
        capability_ids=("records-review",),
        policy_ids=("records-policy",),
    )
    return factory.build(config)


def main() -> None:
    """Run the analyst and print the structured outcome and trace."""

    agent = build_agent()
    outcome = agent.execute(
        principal(),
        "Review record-42",
        execution_id="analyst-execution",
    )
    trace = agent.trace_for(outcome.execution_id)
    assert outcome.status == OutcomeStatus.COMPLETED
    assert trace.policy_decisions
    assert trace.policy_decisions[0].allowed is True
    print(
        json.dumps(
            {
                "status": outcome.status.value,
                "tool_calls": [call.tool_id for call in outcome.tool_calls],
                "trace": {
                    "trace_id": trace.trace_id,
                    "final_status": trace.final_status.value if trace.final_status else None,
                    "policy_allowed": trace.policy_decisions[0].allowed,
                },
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
