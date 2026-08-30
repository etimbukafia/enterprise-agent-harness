# Enterprise Agent Harness

Enterprise Agent Harness is a provider-neutral Python runtime for building, governing, executing, composing, and observing enterprise AI agents.

The project is independent of any one application or domain. It is intended to provide reusable infrastructure for agents that use read and write tools, run bounded or long-lived workflows, require policy and permission checks, support human approval gates, emit auditable traces, and can be registered and composed through stable contracts.

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

See [`docs/BUILD_PLAN.md`](docs/BUILD_PLAN.md) for the phase-by-phase implementation plan.

The Phase 0B architecture baseline and completed Phase 1–2 boundaries are in
[`docs/architecture.md`](docs/architecture.md).
The product scope is in [`docs/product-brief.md`](docs/product-brief.md), the
public API baseline is in [`docs/public-api.md`](docs/public-api.md), and the
development commands are in [`docs/development.md`](docs/development.md).

## Phase 0A implementation

Phase 0A provides a provider adapter, typed read/write/action tool boundary,
deny-by-default permission checks, bounded execution, trust-labelled context,
deterministic safety rules, versioned workflow state, structured audit and
trace sinks, generic outcome verification, and trace/replay contracts.

See the [source audit](docs/PHASE_0A_SOURCE_AUDIT.md), [migration matrix](docs/PHASE_0A_MIGRATION_MATRIX.md),
and [architecture notes](docs/architecture.md) for provenance and scope.

The core package does not include an evaluator. A consumer or external
evaluation system can inspect the exported run trace.

## Phase 1–2 implementation

Phase 1 provides declarative agent, capability, policy, tool, identity,
execution, action, approval, outcome, and error contracts. Phase 2 provides
typed interpretation/planning/composition requests and responses, provider
normalization, deterministic and optional OpenAI adapters, timeout/retry hooks,
and provider metadata in exported traces.

See [`docs/public-api.md`](docs/public-api.md) for the supported imports and
[`docs/providers.md`](docs/providers.md) for provider integration guidance, and
[`docs/development.md`](docs/development.md) for local quality commands.
