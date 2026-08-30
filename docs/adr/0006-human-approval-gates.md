# ADR 0006: Gate exact actions with resumable human approval

Status: accepted

## Context

Some tools can create sensitive or irreversible side effects. A permission
decision can require review, but a later provider response must not change the
action that a reviewer saw.

The application owns approval authority and the review interface. The runtime
must provide a small boundary that preserves the action, review context,
decision lifetime, and execution evidence.

## Decision

The runtime uses two application-owned boundaries:

- `ApprovalPolicyEvaluator` determines whether approval is required. A
  declarative rule can select a tool ID, action ID or kind, risk level, and
  environment. Rules can add a requirement. They cannot remove a requirement
  declared by a tool or trusted permission decision.
- `ApprovalBroker` stores an `ApprovalRequest` and returns an
  `ApprovalDecision`. `InMemoryApprovalBroker` provides a thread-safe
  implementation for tests and one-process integrations.

An approval request contains:

- the exact tool ID and version;
- canonical action arguments and their digest;
- the execution, principal, and tenant identity;
- the original action purpose;
- the compiled review context; and
- the request expiry.

When no decision is available, `AgentRuntime.execute` returns `escalated`,
records `approval_requested`, and keeps the execution resumable. The
application records an approve, reject, or request-change decision. Resume
checks the request ID when present, the exact action digest, the request
expiry, and the decision expiry before a handler can run.

An approved resume continues the stored bounded plan from the paused step with
the exact approval digest in the trusted execution context. It does not re-run
earlier tool steps or allow the provider to replace the reviewed action during
resume. A new action can be reviewed only through a new request. This
preserves the approval boundary without trusting provider state.

Review outcomes map to runtime outcomes as follows:

| Approval decision | Runtime outcome |
| --- | --- |
| approved | Continue the governed execution. |
| rejected | `refused`. |
| request changes | `needs_input`. |
| expired or stale | No handler call; return `escalated` for expiry or `refused` for stale evidence. |

Every approval policy evaluation, request, decision, pause, resume, stale
check, and expiry creates a structured trace or audit event. The trace stores
IDs, digests, statuses, and timestamps. It does not store raw action values in
event metadata.

## Consequences

- High-risk actions can pause without reaching a handler and can resume after
  an exact approval.
- A decision cannot grant authority to another tool, version, or argument
  payload.
- Reviewer decisions are immutable within the in-memory broker.
- A process-local broker does not provide approval-decision restart recovery
  or distributed coordination. Phase 7 adds durable workflow checkpoints,
  while the consumer remains responsible for durable approval storage.
- Applications must treat an approved write as an ordinary side effect and
  keep the tool idempotent where duplicate execution can cause harm.

## Rejected alternatives

- Passing a boolean approval flag from provider output would let untrusted
  data change an application control.
- Approving a tool ID without its version and argument digest would allow
  approval reuse for a different side effect.
- Re-running a changed action under the old approval would violate the exact
  review boundary.
- Making the core package own a human-review UI or organization policy would
  move application responsibilities into the provider-neutral runtime.
