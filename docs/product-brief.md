# Product brief

Status: accepted product scope; implementation baseline through Phase 2.

## Product

Enterprise Agent Harness is a provider-neutral Python runtime for enterprise
agents. It gives a consuming application a typed boundary for agent plans,
tool calls, policy decisions, workflow state, and execution evidence.

The runtime treats model output as a proposal. The consuming application and
the runtime decide which work is allowed. The runtime can run read tools,
write tools, and action tools through the same governed execution path.

The harness is a library. It is not an end-user application.

## Problem

An enterprise application needs more than a model call. It must control
identity, authority, side effects, approvals, state, and evidence. These
controls must remain stable when the application changes model providers.

Without a shared runtime boundary, each application can implement these
controls differently. This makes unsafe tool use, hidden provider coupling,
and incomplete audit evidence more likely.

## Users

- Application teams integrate the runtime with business systems.
- Tool owners publish typed domain operations.
- Governance owners define permissions, policy, risk, and approval rules.
- Platform operators run and observe executions.
- External evaluation systems inspect exported traces.

These users can be in one organization. The harness does not require one
deployment model or one data store.

## Product responsibilities

The target product provides:

- typed and versioned agent, capability, tool, policy, and runtime contracts;
- provider adapters that return proposal data;
- bounded plan-and-act execution;
- validation before every tool handler;
- deny-by-default permission and policy boundaries;
- explicit approval boundaries for sensitive actions;
- separate workflow state and optional memory;
- structured trace and audit output;
- registry and factory boundaries for reuse and composition; and
- stable export contracts for external evaluation and replay.

The build plan defines when each responsibility becomes executable. A
baseline document does not claim that a later-phase feature is complete.

## Supported execution modes

The runtime must support these modes through one governance path:

| Mode | Required property |
| --- | --- |
| Read | The operation returns typed data and has no intended business side effect. |
| Write | The operation can change data and declares idempotency behavior when a duplicate can cause harm. |
| Background action | A scheduled or event-triggered execution uses an explicit principal, tenant, trigger, and execution identity. |
| Approval-gated action | The exact tool version and argument digest are approved before the handler runs. |

The application owns the business meaning of each operation. The runtime
enforces the declared contract and the configured safety boundary.

## Product outcomes

An integration is successful when:

1. A provider can propose a plan without being able to grant authority.
2. Invalid, unauthorized, or unapproved tool calls do not reach a handler.
3. The same agent contract can use more than one provider adapter.
4. A run has a bounded execution path and a deterministic final outcome.
5. State and memory do not become an accidental authority channel.
6. An operator can reconstruct important runtime decisions from structured
   trace and audit data.
7. An external evaluator can consume the exported trace without importing
   private runtime code.

## Explicit non-goals

The core package does not provide:

- a chat UI, conversation product, or assistant response format;
- a domain workflow, business rule set, or resource authorization database;
- authentication, an identity provider, or a tenant control plane;
- business data storage, external API credentials, or secret management;
- a model, model training, inference service, or provider SDK as a required
  dependency;
- arbitrary generated code or unrestricted code execution;
- a complete evaluation, grading, baseline, or agent-improvement system;
- a durable distributed queue, scheduler, lock service, or job platform;
- a universal knowledge store or mandatory memory implementation;
- a default human approval service or approval user interface;
- autonomous production deployment or release management; or
- a guarantee that a model or tool result is factually correct.

The consuming application or an external system supplies these capabilities
through explicit interfaces when it needs them.

## Boundary assumptions

The consuming application supplies an authenticated principal, tenant
boundary, business policy, resource authorization data, tool handlers, and
provider configuration. It also decides where durable state, memory, audit,
and trace data are stored.

The runtime supplies the execution boundary. It validates proposal data,
applies its safety ceiling, invokes policy and approval interfaces, and emits
structured evidence. No provider response can replace these controls.

## Release relationship

The v0.1 milestone is the first coherent product target. It requires a
declarative agent, registry resolution, bounded tool execution, permission
checks, approval for a write action, structured traces, provider replacement,
and external trace replay. The milestone does not make the core package a
complete production platform.
