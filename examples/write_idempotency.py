"""Governed write-tool example with an explicit idempotency key.

Run from the repository root with:

    python -m examples.write_idempotency

The fixed plan provider is a small example fixture. A production provider
must produce an idempotency key from the business operation.
"""

from __future__ import annotations

from enterprise_agent_harness import (
    AgentPlan,
    AgentTemplate,
    DeterministicProvider,
    PlanningRequest,
    PlanningResponse,
    PlanStep,
    ProviderCallMetadata,
    ToolResultStatus,
)

from ._support import (
    TextOutput,
    agent_config,
    allow_policy,
    make_factory,
    principal,
    write_tool,
)


class FixedWriteProvider(DeterministicProvider):
    """Return the same business idempotency key for repeated submissions."""

    def plan(self, *, request: PlanningRequest) -> PlanningResponse:
        tool = next(item for item in request.tools if item.tool_id == self.tool_id)
        return PlanningResponse(
            plan=AgentPlan(
                steps=[
                    PlanStep(
                        step_id="write-step",
                        tool_id=tool.tool_id,
                        tool_version=tool.version,
                        purpose="Write the requested record once.",
                        arguments={"query": request.context.input_text},
                        idempotency_key="record-42-publish",
                    )
                ]
            ),
            metadata=ProviderCallMetadata(
                provider_id=self.provider_id,
                provider_version=self.provider_version,
                model=self.model,
                request_id=request.request_id,
            ),
        )


def main() -> None:
    """Run the same write request twice and show one handler call."""

    calls: list[str] = []
    tool = write_tool(
        handler=lambda _context, arguments: (
            calls.append(arguments.query) or TextOutput(value=f"stored: {arguments.query}")
        ),
    )
    provider = FixedWriteProvider(tool_id=tool.tool_id)
    factory, _tools, _skills, _traces, _audits = make_factory(
        [tool],
        policies=[allow_policy(tool.tool_id)],
        provider=provider,
    )
    agent = factory.build(
        agent_config(
            "records-writer",
            tool_id=tool.tool_id,
            template=AgentTemplate.ACTION_AGENT,
            risk_level=tool.risk_level,
            policy_ids=("records-policy",),
        )
    )
    owner = principal()
    first = agent.execute(
        owner,
        "Store record-42",
        granted_permissions=["records:write"],
        execution_id="write-execution-1",
    )
    second = agent.execute(
        owner,
        "Store record-42",
        granted_permissions=["records:write"],
        execution_id="write-execution-2",
    )
    assert first.status.value == "completed"
    assert second.status.value == "completed"
    assert calls == ["Store record-42"]
    assert second.tool_calls[0].result_status == ToolResultStatus.SUCCEEDED
    print(f"first status: {first.status.value}")
    print(f"second status: {second.status.value}")
    print(f"handler calls: {len(calls)} (the expected value is 1)")


if __name__ == "__main__":
    main()
