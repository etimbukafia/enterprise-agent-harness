# ADR 0005: Bounded execution controls

Status: accepted

## Context

An agent run can call a provider and several tools. A provider can return a
large plan. A provider or handler can also fail, retry, or wait for an
unbounded time. The runtime must close every run with a deterministic outcome.

Python cannot force-stop a synchronous function that is already running in a
worker thread. The runtime can stop waiting, but the application must control
the remote operation and make side effects idempotent.

## Decision

`AgentRuntime` uses these controls for one run:

- `RuntimeConfig.max_plan_steps` limits the number of proposed tool steps.
- `RuntimeConfig.execution_timeout_seconds` sets a monotonic run deadline.
- `RuntimeConfig.max_retries` sets one retry budget shared by provider and tool
  calls.
- `CancellationToken` or a standard `threading.Event` lets the caller request
  cooperative cancellation.

The runtime checks controls before each provider call and tool step. Provider
and tool wait boundaries also observe the deadline and cancellation signal.
The runtime returns `timed_out` or `cancelled` and records the control event
when a run stops for either reason.

An empty provider plan returns `needs_input`. A plan above the step limit is
refused before a handler can run. Tool failures remain in the typed result
list so the safety policy can return a safe partial outcome when another tool
succeeds.

## Consequences

Every run has a finite plan, time, and retry boundary. Retry behavior is
replayable from trace events and tool execution records. A timed-out or
cancelled worker can still finish outside the runtime wait boundary. Production
consumers must use cancellation-aware provider and tool clients and durable
idempotency for side-effecting operations.
