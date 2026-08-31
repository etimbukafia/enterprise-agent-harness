"""Event-driven execution with duplicate-safe handling.

Run from the repository root with:

    python -m examples.event_driven

The in-memory runner is for local examples. A production consumer must provide
durable queue, lease, and deduplication implementations.
"""

from __future__ import annotations

from enterprise_agent_harness import (
    BackgroundJobRunner,
    EventDisposition,
    EventEnvelope,
    EventTrigger,
)

from ._support import agent_config, allow_policy, capability, make_factory, principal, read_tool


def main() -> None:
    """Handle an event and submit the same event again."""

    tool = read_tool()
    factory, _tools, _capabilities, _traces, _audits = make_factory(
        [tool],
        capabilities=[capability(tool.tool_id)],
        policies=[allow_policy(tool.tool_id)],
    )
    agent = factory.build(
        agent_config(
            "billing-event-agent",
            tool_id=tool.tool_id,
            capability_ids=("records-review",),
            policy_ids=("records-policy",),
        )
    )
    assert agent.runtime is not None
    runtime = agent.runtime
    owner = principal()
    event = EventEnvelope(
        event_id="invoice-created-42",
        event_type="invoice.created",
        source="billing",
        payload={"invoice_id": "42"},
        correlation_id="billing-correlation-42",
        deduplication_key="invoice-42",
        principal_id=owner.principal_id,
        tenant_id=owner.tenant_id,
    )
    trigger = EventTrigger(
        trigger_id=event.trigger_id,
        event_type=event.event_type,
        source=event.source,
        agent_id=agent.manifest.agent.agent_id,
        agent_version=agent.manifest.agent.version,
    )
    tool_ids = [item.tool_id for item in agent.manifest.tools]
    tool_versions = [f"{item.tool_id}@{item.version}" for item in agent.manifest.tools]

    def handle(
        principal,
        input_text,
        *,
        correlation_id,
        event_id,
        trigger_id,
        causation_id,
        attempt,
        execution_id,
    ):
        del correlation_id, event_id, trigger_id, causation_id
        return runtime.execute_event(
            event,
            principal=principal,
            input_text=input_text,
            trigger=trigger,
            attempt=attempt,
            execution_id=execution_id,
            agent_id=agent.manifest.agent.agent_id,
            agent_version=agent.manifest.agent.version,
            authorized_tool_ids=tool_ids,
            authorized_tool_versions=tool_versions,
        )

    runner = BackgroundJobRunner(handle)
    first = runner.run(event, principal=owner, input_text="Review invoice 42")
    duplicate = runner.run(event, principal=owner, input_text="Review invoice 42")
    assert first.disposition == EventDisposition.COMPLETED
    assert duplicate.disposition == EventDisposition.DUPLICATE
    assert first.execution_id is not None
    trace = agent.trace_for(first.execution_id)
    assert trace.event_id == event.event_id
    assert trace.correlation_id == event.correlation_id
    print(f"first disposition: {first.disposition.value}")
    print(f"duplicate disposition: {duplicate.disposition.value}")
    print(f"trace correlation: {trace.correlation_id}")


if __name__ == "__main__":
    main()
