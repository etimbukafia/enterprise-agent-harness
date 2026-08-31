# Enterprise Agent Harness

Enterprise Agent Harness is a provider-neutral Python runtime for building,
governing, executing, composing, and observing enterprise AI agents.

The project is independent of any one application or domain. It provides
reusable infrastructure for agents that use read and write tools, run bounded
or long-lived workflows, require policy and permission checks, support human
approval gates, emit auditable traces, and can be registered and composed
through stable contracts.

The design generalizes proven boundary patterns from `ai-assistant-harness`,
but this repository is a separate system with broader enterprise-agent
requirements.

## Core principles

- Application code owns identity, permissions, policy, and deployment authority.
- Models may propose actions, but deterministic runtime controls decide whether actions may execute.
- Agents are assembled from versioned contracts and reusable components rather than generated as arbitrary code.
- Tool use is typed, permissioned, observable, and auditable.
- Human approval is explicit for sensitive or irreversible actions.
- Agent and tool registries make reuse, extension, and composition first-class.
- Provider adapters remain replaceable.
- Runtime traces are structured so external evaluation and improvement systems can consume them.

## Build plan

See [`docs/BUILD_PLAN.md`](docs/BUILD_PLAN.md) for the phase-by-phase
implementation plan.

The Phase 0B architecture baseline and completed Phase 1-10 boundaries are in
[`docs/architecture.md`](docs/architecture.md). The product scope is in
[`docs/product-brief.md`](docs/product-brief.md), the public API baseline is in
[`docs/public-api.md`](docs/public-api.md), and the development commands are
in [`docs/development.md`](docs/development.md).

## Quickstart and examples

Start with the [quickstart](docs/quickstart.md). It builds and runs one
governed read-only agent with the deterministic provider.

The `examples/` directory contains small runnable patterns for read tools,
idempotent writes, approval, delegation, event handling, factory use, registry
queries, and external evaluation. Run an example from the repository root:

```text
python -m examples.quickstart
```

## Phase 0A implementation

Phase 0A provides a provider adapter, typed read/write/action tool boundary,
deny-by-default permission checks, bounded execution, trust-labelled context,
deterministic safety rules, versioned workflow state, structured audit and
trace sinks, generic outcome verification, and trace/replay contracts.

See the [source audit](docs/PHASE_0A_SOURCE_AUDIT.md), [migration
matrix](docs/PHASE_0A_MIGRATION_MATRIX.md), and [architecture
notes](docs/architecture.md) for provenance and scope.

The core package does not include an evaluator. A consumer or external
evaluation system can inspect the exported run trace.

## Phase 1-15 implementation

Phase 1 provides declarative agent, capability, policy, tool, identity,
execution, action, approval, outcome, and error contracts. Phase 2 provides
typed interpretation, planning, and composition requests and responses,
provider normalization, deterministic and optional OpenAI adapters,
timeout/retry hooks, and provider metadata in exported traces.

Phase 3 provides versioned tool registration and lifecycle controls, typed
argument and result validation, timeout and explicit retry controls,
idempotency-key handling, dependency metadata, and redacted tool execution
records. Phase 4 provides principal and agent allowlists, declarative
deny-by-default policy, resource hooks, environment and risk limits, explicit
policy records, and runtime enforcement of the authority ceiling. Phase 5
provides the bounded runtime loop, trust partitions, run timeout and
cancellation controls, a shared retry budget, deterministic stop states, and
replayable execution traces. Phase 6 provides versioned approval policies,
exact action requests with review context, pause and resume behavior, expiry,
review outcomes, and approval transition audit events. Phase 7 provides
principal-bound in-memory and SQLite workflow state, JSON checkpoints,
optimistic concurrency, retention hooks, and restart hydration for paused
executions. Phase 8 provides versioned agent and capability registries with
metadata search, lifecycle controls, compatibility checks, dependency graphs,
read-only queries, deterministic snapshots, and audit events.

Phase 9 provides declarative `AgentConfig` resolution, reusable runtime
profiles, standard agent templates, immutable resolved manifests, dry-run
validation, and registration of factory-built runtimes. Phase 10 provides
runtime-only agent delegation, parent-child authority ceilings, exact tool
version propagation, bounded router/supervisor/specialist/sequential
composition, maximum depth, cycle detection, and shared trace/audit
correlation.

Phase 11 provides event-driven and background execution: a provider-neutral
event envelope with distinct event, trigger, correlation, and dedup identities;
`AgentRuntime.execute_event`; and a `BackgroundJobRunner` with lease, dedup,
bounded retry, cancellation, and dead-letter controls on the same governed
runtime. Phase 12 provides structured audit and trace sinks, attributable
metrics and a configurable cost model, trusted execution budgets, configurable
redaction, and stable correlation across retries and resumptions.

See [`docs/public-api.md`](docs/public-api.md) for supported imports,
[`docs/providers.md`](docs/providers.md) for provider integration guidance,
and [`docs/development.md`](docs/development.md) for local quality commands.

Phase 14 adds an external evaluation integration contract. Phase 15 adds the
quickstart, reference examples, production extension-point guidance, and
runtime flow diagrams. The examples use local deterministic components. They
are not production deployments.
