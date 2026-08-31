# ADR 0010: Bounded composition and delegation

Status: accepted

## Context

Multiple agents need to cooperate, but a child must not become a privilege
amplification path. Peer invocation also needs to remain observable as one
parent-child execution tree and must use the same tool, policy, approval, and
state controls as a direct run.

## Decision

1. `DelegationRequest`, `DelegatedExecutionContext`, and `DelegationResult`
   identify exact parent and child agent versions and carry the principal,
   execution, correlation, authority, risk, step, depth, and path data needed
   for a governed child run.
2. `AgentComposer` invokes only active, factory-built child runtimes. It never
   calls a child provider or tool handler directly.
3. Child tool authority is the intersection of parent-authorized tool IDs and
   exact `tool_id@version` references with the child resolved manifest.
   Delegated permissions are a subset of parent grants. A child tool’s
   required permissions must be within those grants. A child risk level above
   the parent risk ceiling is rejected, and child steps use the smaller parent
   and child limits.
4. Approval evidence is not inherited. Any child side effect must pass the
   child runtime’s own exact approval gate.
5. Child executions retain the parent correlation ID and record parent
   execution ID, delegation ID, positive depth, and an identity path. The
   composer rejects an identity already in the path and rejects a configured
   maximum-depth overflow. A delegation ID is single-use within its parent.
   Each delegation receives a separate generated child execution ID and state
   ID. The composer rejects generated identity reuse before child execution.
6. Supported composition patterns are router and specialist selection of one
   child, supervisor fan-out across declared children, and sequential
   execution that passes a completed child summary to the next child and stops
   on a non-completed result.

## Consequences

Delegation is safe to expose as a reusable runtime capability because it cannot
increase the parent authority snapshot. Shared trace and audit correlation
lets consumers reconstruct the composed run tree. A parent must explicitly
carry exact tool-version authority when it delegates tool work; an old context
that contains only tool IDs cannot silently broaden a child to arbitrary
versions.

The composition coordinator does not decide business outcomes for a child. It
returns runtime-owned child outcomes and a deterministic aggregate selection;
the consuming application still owns business routing and durable orchestration
policy.

## Rejected alternatives

- Direct child handler or provider calls would bypass runtime validation,
  policy, approval, state, and trace controls.
- Passing the parent’s full authority without intersection would allow a child
  manifest to turn delegation into privilege amplification.
- Inheriting approval digests would let an approval for a parent action approve
  an unrelated child action.
- Unbounded recursive delegation would make cost, latency, and audit scope
  unpredictable.
