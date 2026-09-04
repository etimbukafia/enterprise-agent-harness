"""Approval-gated action example.

Run from the repository root with:

    python -m examples.approval_gated_action
"""

from __future__ import annotations

from enterprise_agent_harness import (
    AgentTemplate,
    InMemoryApprovalBroker,
    OutcomeStatus,
)

from ._support import (
    TextOutput,
    action_tool,
    agent_config,
    allow_policy,
    make_factory,
    principal,
)


def main() -> None:
    """Pause at approval, approve the exact request, and resume."""

    published: list[str] = []
    tool = action_tool(
        handler=lambda _context, arguments: (
            published.append(arguments.query) or TextOutput(value=f"published: {arguments.query}")
        )
    )
    broker = InMemoryApprovalBroker()
    factory, _tools, _skills, _traces, _audits = make_factory(
        [tool],
        policies=[allow_policy(tool.tool_id)],
        approval_broker=broker,
    )
    agent = factory.build(
        agent_config(
            "records-operator",
            tool_id=tool.tool_id,
            template=AgentTemplate.APPROVAL_GATED_OPERATOR,
            risk_level=tool.risk_level,
            policy_ids=("records-policy",),
        )
    )
    owner = principal()
    paused = agent.execute(
        owner,
        "Publish record-42",
        granted_permissions=["records:publish"],
        environment="production",
        execution_id="approval-execution",
    )
    assert paused.status == OutcomeStatus.ESCALATED
    request = broker.pending_requests[0]
    print(f"paused: {paused.status.value}")
    print(f"approval request: {request.request_id}")
    print(f"action digest: {request.action_digest}")

    broker.approve(request.request_id, decided_by="reviewer-1")
    assert agent.runtime is not None
    completed = agent.runtime.resume(paused.execution_id, principal=owner)
    assert completed.status == OutcomeStatus.COMPLETED
    assert published == ["Publish record-42"]
    print(f"resumed: {completed.status.value}")
    print(f"handler calls: {len(published)}")


if __name__ == "__main__":
    main()
