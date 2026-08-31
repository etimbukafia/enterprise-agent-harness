# ADR 0011: Event-driven and background execution

Status: accepted

## Context

Agents must respond to events and scheduled work, not only to interactive
calls. Background execution must reuse the same governed runtime path and must
not weaken the permission, policy, approval, budget, state, trace, or delegation
controls established in Phases 1 through 10.

## Decision

1. Event delivery uses a provider-neutral `EventEnvelope`. It separates four
   identities instead of overloading one value: `event_id` (one delivered
   event), `trigger_id` (derived from event type and source), `correlation_id`
   (one logical work item, stable across retries, resumptions, and
   delegations), and `deduplication_key` (idempotency identity). `causation_id`
   records an optional parent event or execution. The raw payload is never
   stored in traces or audit; only a non-reversible `payload_digest` is
   exported.
2. `AgentRuntime.execute_event` is the only event entry point. It resolves the
   input text from an extractor or a deterministic default, forces the
   event-derived correlation fields, and calls the existing `execute` path.
   It does not bypass permission, policy, approval, budget, state, trace,
   audit, tool-validation, or delegation controls.
3. `ExecutionContext`, `RunTrace`, and `AuditEvent` carry optional `event_id`
   (the audit field is `source_event_id` because the record already owns its
   own `event_id`), `trigger_id`, `causation_id`, and `attempt`.
4. Lease semantics are an explicit `LeaseStore` extension point with an
   `InMemoryLeaseStore` for tests. Acquire on a free or expired key succeeds;
   acquire on an owned, unexpired key raises `LeaseConflictError`; release by a
   non-owner fails closed; renewal only extends an owned lease. The background
   runner uses a unique claim owner for each delivery and records the lease
   version with the deduplication claim. The in-memory store is single-process
   and does not provide distributed-lock guarantees.
5. Event deduplication is an explicit `DeduplicationStore` extension point with
   an `InMemoryDeduplicationStore`. A delivery is marked `IN_PROGRESS` only
   with its exact lease owner and version. Terminal transitions use the same
   claim identity, so a stale worker cannot overwrite a newer result.
   `PENDING_APPROVAL` is non-terminal and prevents duplicate delivery while a
   reviewer decides. Write-tool idempotency guards one side-effecting call
   inside the governed execution. The two mechanisms complement each other;
   neither replaces the other.
6. `BackgroundJobRunner` owns dedup lookup, lease acquisition, bounded retry,
   cancellation, dead-letter disposition, and event audit correlation. It calls
   an application `JobHandler`, which is expected to route through
   `AgentRuntime.execute_event`. Each retry is a new `execution_id` but keeps
   the same `correlation_id`, `event_id`, `trigger_id`, and an incremented
   `attempt`. Retries are bounded and only retryable transient failures; a
   completed irreversible action is never retried. Permanent failures and
   budget/cancellation exhaustion produce a structured `JobResult` and, when
   appropriate, a `DeadLetterRecord`. `resolve_pending()` commits the result of
   an approval resume without invoking the event handler again.
7. Event lifecycle audit events carry the correlation and attempt data, so
   event → trigger → execution → policy → approval → tool → state → outcome can
   be reconstructed across retries and resumptions.

## Consequences

The same runtime executes interactive and event-driven agents. Duplicate event
delivery cannot cause duplicate irreversible actions. The core package defines
contracts and deterministic in-memory implementations; production distributed
queues, schedulers, locks, and durable dedup storage remain consumer
boundaries.

## Rejected alternatives

- A separate background runtime would duplicate governance and risk divergence
  from the interactive path.
- Overloading `event_id` for correlation, dedup, and execution would make
  retries and resumptions ambiguous.
- Auto-retrying after a write/action tool already succeeded could duplicate an
  irreversible effect.
- Claiming distributed-lock guarantees from an in-memory store would be unsafe.

## Correlation model

| Identity | Meaning | Scope |
| --- | --- | --- |
| `event_id` | One delivered event | Per delivery |
| `trigger_id` | Derived from event type and source | Per trigger |
| `correlation_id` | One logical work item | Across retries, resumptions, delegations |
| `deduplication_key` | Idempotency identity | Per event |
| `causation_id` | Optional parent event or execution | Optional |
