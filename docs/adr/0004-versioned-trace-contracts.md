# ADR 0004: Make traces structured, versioned, and evaluation-neutral

- Status: accepted
- Date: 2026-08-30

## Context

Operators need evidence for runtime decisions. External evaluation systems
need a stable input. Raw prompts, tool output, credentials, and secret values
must not become the default trace payload.

The harness is not an evaluation platform. It must export evidence without
owning grading policy or promotion decisions.

## Decision

The runtime emits a versioned `TraceEvent` stream and an exported `RunTrace`.
Events cover provider calls, plan proposals and validation, permission and
policy decisions, approval transitions, tool validation and results, state
transitions, retries, failures, delegation, budgets, and final outcomes as
those features become available.

The trace carries stable IDs, sequence numbers, execution and component
versions, outcome status, safe metadata, and non-reversible fingerprints when
an input or argument needs correlation. Raw input and raw tool output are not
included by default. Consumers can supply a sink and controlled redaction
policy.

Trace and replay schemas use explicit contract versions. Additive optional
fields preserve a schema major version. A change that alters field meaning or
requires a consumer change uses a new schema major version. Unknown future
fields must not be treated as authority.

The evaluation package contains only runtime-facing trace and replay
contracts. Cases, graders, metrics, baselines, and promotion policy remain in
an external system.

## Consequences

- Independent systems can consume traces without private imports.
- Redaction and fingerprint rules are part of the safety boundary.
- Trace storage and retention remain consumer responsibilities.
- New runtime features must add trace coverage before they are considered
  complete.
- External evaluators must handle the declared schema versions.

## Rejected alternatives

- Prose logs alone cannot support deterministic replay or structured audit.
- Storing raw prompts by default increases data exposure.
- Embedding graders in the runtime would move evaluation ownership across the
  product boundary.
