# Enterprise Agent Harness

Enterprise Agent Harness is a provider-neutral Python runtime for building, governing, executing, composing, and observing enterprise AI agents.

The project is independent of any one application or domain. It is intended to provide reusable infrastructure for agents that use read and write tools, run bounded or long-lived workflows, require policy and permission checks, support human approval gates, emit auditable traces, and can be registered and composed through stable contracts.

The design evolves proven ideas from `ai-assistant-harness`, but this repository is a separate system with broader enterprise-agent requirements.

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
