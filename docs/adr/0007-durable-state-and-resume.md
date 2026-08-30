# ADR 0007: Durable workflow state and restartable execution

Status: accepted

## Context

An agent execution can pause while waiting for approval or another external
decision. Process-local dictionaries are useful for tests, but they cannot
support a runtime restart or provide a concurrency boundary for multiple
workers. Workflow state also has a different authority and retention meaning
from optional conversational or retrieval memory.

The runtime must preserve enough trusted continuation data to resume the exact
reviewed workflow. It must not turn a provider proposal, memory item, or
retrieved document into new authority during hydration.

## Decision

1. `StateStore` is the public owner-bound workflow-state protocol. It supports
   get-or-create, optimistic saves, owner-filtered execution lookup, and
   retention purging.
2. `InMemoryStateStore` remains the deterministic local implementation.
   `SQLiteStateStore` is the first durable implementation and maintains a
   schema migration table with versioned JSON state rows.
3. State rows are keyed by tenant, session, agent, and state identity. Reads
   and writes verify principal, tenant, and session ownership. A save with an
   expected version fails with `StateConflictError` when another writer has
   advanced the row.
4. SQLite writes use explicit serialized transactions and parameter binding.
   The store owns schema setup and migration bookkeeping, while the consuming
   application owns database access control, encryption, backups, deployment,
   and cross-process job leasing.
5. A paused approval execution is represented by the versioned
   `ExecutionCheckpoint` contract. The checkpoint stores the exact execution
   context, bounded plan, remaining plan, reviewed request, typed prior
   evidence, and redacted trace. Resume hydration requires the same explicit
   principal and runs the remaining plan through ordinary tool, policy,
   permission, approval, and safety checks.
6. The runtime seeds a resumed trace from the checkpoint and removes the
   checkpoint when the execution reaches a terminal outcome. A new pending
   approval receives a new checkpoint containing the merged trace history.
7. TTL and retention hooks are opt-in. The runtime does not claim to provide
   an approval service, encryption, secure deletion, durable tool idempotency,
   or a distributed queue. A process-restart deployment must provide a
   durable approval broker or independently persisted exact approval evidence.

## Consequences

Durable state can survive a runtime object or process restart, and stale
workers fail closed instead of overwriting a newer continuation. State schema
and checkpoint schema versions provide an explicit migration path. The
checkpoint can contain sensitive workflow details, so consumers must apply
their own database security and retention policy.

The default SQLite implementation is appropriate for a first durable boundary,
but high-scale deployments may replace `StateStore` with a transactional
PostgreSQL or service-backed implementation that preserves the same ownership
and version semantics.

## Rejected alternatives

- Treating conversational memory as workflow state would mix retention and
  authority boundaries.
- Replaying the provider after restart would allow a new proposal to replace
  the reviewed plan and would make side effects non-deterministic.
- Last-write-wins persistence would permit a stale worker to erase progress.
- Making the runtime own approval storage would cross the application-owned
  approval authority boundary.
