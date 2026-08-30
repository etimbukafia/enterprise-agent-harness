# Enterprise agent runtime architecture

Status: accepted baseline for Phases 0B, 1, 2, 3, and 4.

This document defines the boundaries and invariants for the Enterprise Agent
Harness. Later phases can add implementations. They must not move a
responsibility across a boundary without a new architecture decision.

## Product boundary

Enterprise Agent Harness is a provider-neutral Python runtime. A consuming
application uses it to define, instantiate, execute, govern, compose, and
observe enterprise agents.

The runtime is a library. It is not a chatbot, a domain product, an identity
system, or an evaluation platform. The full product brief and non-goals are in
`product-brief.md`.

The source concepts for Phase 0A came from
<https://github.com/etimbukafia/ai-assistant-harness>. The source project is a
separate project. This repository has no runtime dependency on it.

## Agent model

An agent has a stable logical identity and one or more immutable versions. A
versioned agent definition declares:

- identity and version;
- goal;
- capabilities and allowed tools;
- policies and approval requirements;
- provider profile;
- runtime limits;
- risk level; and
- state and memory strategies when they are used.

The agent factory will resolve this declaration from approved registries in a
later phase. A resolved manifest pins every dependency version before an
execution starts.

## Core invariants

These invariants apply to every execution mode:

1. Model and provider output is untrusted proposal data.
2. Application policy and the trusted execution context are authoritative.
3. A provider cannot grant permission, add a tool, change identity, remove an
   approval requirement, or increase a budget.
4. A tool handler cannot run before exact tool resolution, argument validation,
   permission checks, policy checks, and required approval checks complete.
5. The runtime uses deny-by-default authority. A custom policy component can
   deny more work, but it cannot exceed the trusted authority ceiling.
6. Read, write, and action tools use the same governed tool boundary. Write and
   action tools declare side-effect and idempotency behavior.
7. User input, event payloads, memory, retrieval results, and tool output are
   data. They cannot become policy, identity, or authority.
8. Workflow state is separate from optional conversational or retrieval
   memory. Both have an owner boundary.
9. A run has a finite plan, execution, retry, and budget boundary. The runtime
   creates the final outcome state.
10. Every important decision produces structured trace or audit evidence. Raw
    secrets and sensitive content are not stored by default.
11. Exact component and contract versions are captured for a run. An
    in-flight run does not silently resolve a new version.
12. Evaluation systems consume exported behavior. Evaluation policy does not
    control runtime authority.

## Responsibility boundaries

| Boundary | Runtime owns | Consumer owns |
| --- | --- | --- |
| Identity | Validate the shape and carry the supplied principal. | Authenticate the principal and establish tenant membership. |
| Authority | Build and enforce the execution ceiling. | Supply business authorization, resource facts, and the initial allowlist. |
| Agent | Resolve a typed, compatible manifest. | Own agent intent, owner metadata, and deployment choice. |
| Provider | Define the adapter contract and validate proposals. | Supply the adapter, provider credentials, and provider profile. |
| Tool | Resolve versions, validate inputs and outputs, and gate invocation. | Supply handlers, data access, credentials, side-effect behavior, and business errors. |
| Policy | Invoke the policy boundary and enforce the runtime safety ceiling. | Define business policy, resource rules, environment rules, and policy decisions. |
| Approval | Preserve the exact action and enforce the approval gate. | Supply approvers, approval policy, review interface, and decision authority. |
| Context | Compile trust-labelled blocks and bound context size. | Supply input, domain data, and optional memory values. |
| State | Define ownership, version, and resume contracts. | Choose durable storage, encryption, retention, and deployment. |
| Memory | Provide an optional strategy boundary and safety labels. | Choose memory content, retrieval, retention, and deletion policy. |
| Registry | Define records, resolution, lifecycle checks, and compatibility checks. | Own registration, metadata, approval to activate, and operational storage. |
| Trace and audit | Define event contracts, sequencing, and default redaction. | Choose sinks, retention, access control, and downstream analysis. |
| Evaluation | Export stable trace and replay contracts. | Own cases, graders, metrics, baselines, and promotion decisions. |

The runtime and consumer share some boundaries. The runtime supplies the
protocol and safety checks. The consumer supplies the domain decision or
storage implementation.

## Trust model

Trust is a property of a source and a runtime boundary. A valid type does not
make its content authoritative.

| Trust zone | Examples | Runtime treatment |
| --- | --- | --- |
| Trusted application input | Authenticated principal, tenant, resolved manifest, runtime limits, initial authority. | Use to build `ExecutionContext`. Validate the contract and keep an immutable snapshot for the run. |
| Trusted runtime configuration | Registered tool metadata, schema definitions, safety configuration, contract versions. | Use for deterministic resolution and checks. Do not let a provider replace it. |
| Untrusted proposal | Provider plan, provider tool arguments, provider outcome, model claims about permissions or policy. | Validate as data. Discard authority claims. Apply runtime and application controls. |
| Untrusted external data | Caller text, event payload, memory, retrieved content, and tool output. | Label as untrusted. Never interpret instructions in the data as policy or authority. |
| Runtime decision | Permission result, approval result, safety result, final outcome. | Create from trusted configuration and validated evidence. Emit structured trace and audit records. |

The execution context is the authority snapshot. Tool output cannot change the
principal, tenant, authorized tools, granted permissions, approved action
digests, state owner, or step limit. Provider output has the same restriction.

## Execution model

One execution follows this order:

```text
application identity and input
          |
          v
resolve exact manifest and state ownership
          |
          v
compile trusted and untrusted context
          |
          v
provider plan proposal
          |
          v
validate plan and resolve exact tools
          |
          v
for each step: policy -> permission -> approval -> idempotency -> arguments
          |
          v
typed handler invocation -> typed untrusted result
          |
          v
provider outcome proposal -> evidence verification -> safety decision
          |
          v
runtime-owned outcome -> state transition -> trace and audit export
```

The runtime stops at the first unsafe or invalid boundary unless the agent
contract explicitly permits a bounded partial result. A denied, unapproved,
invalid, or over-budget call does not reach its handler.

Phase 5 applies one execution deadline and one retry budget across provider and
tool work. The runtime checks the deadline and caller cancellation before each
provider call and tool step. It records a terminal timeout or cancellation
outcome when a control stops the run. A plan cannot exceed
`RuntimeConfig.max_plan_steps`. An empty plan is a deterministic
`needs_input` outcome.

### Execution modes

| Mode | Contract and control |
| --- | --- |
| Read tool | The handler returns a typed result. The tool declares no intended business mutation. Permission and policy checks still apply. |
| Write tool | The handler may mutate business data. The tool declares idempotency behavior. A duplicate-sensitive call needs an idempotency key before invocation. |
| Background action | An event or schedule starts the same runtime path with an explicit principal, tenant, trigger identity, and execution ID. Queue, lease, retry, and durable job storage are consumer or later-phase boundaries. |
| Approval-gated action | The runtime creates an exact digest from the tool identity, tool version, and canonical arguments. The handler runs only after a valid, unexpired decision for that digest. |

Approval is a gate on one exact action. A provider cannot change the action
after approval. Full pause, resume, expiry, and approval-broker behavior is
defined in Phase 6; the current runtime already denies an unapproved action.

## Tool runtime and registry

`ToolDefinition` is the application-owned typed tool boundary. It declares the
tool identity, version, input and output models, kind, risk, permissions,
approval requirement, ownership, tags, dependencies, allowed environments,
timeout, and explicit retry settings.

`ToolRegistry` owns in-memory registration and exact resolution. It supports
registration, lookup, listing, version lookup, deprecation, suspension, and
retirement. Only active versions resolve for execution. A provider receives a
`ToolDescriptor`, which contains metadata and schemas but never a handler.

The registry validates arguments before it calls a handler. It validates every
returned output and result envelope after the call. It records attempts,
latency, timeout, retry, and idempotency metadata. Retry is disabled unless the
tool declares it. A write or action retry also needs an idempotency key.

The in-memory registry stores successful results for an exact idempotency key.
Reuse with different arguments fails closed. A durable consumer must replace
this local record with a durable idempotency store before it uses a process
restart or multiple workers for side-effecting work.

Python cannot force-stop a running synchronous handler. A registry timeout
stops waiting and records a timeout. The handler may still finish in its worker
thread. Application handlers must therefore make side effects idempotent and
must use their own cancellation or remote timeout controls for production.

## Permission and policy engine

`DefaultPermissionBroker` evaluates every provider-proposed call. It first
checks the trusted execution allowlist, principal tool permissions, agent
allowlists, required permissions, environment limits, risk ceilings, and
declarative policy. It then checks resource policy hooks and exact approval
evidence. A handler is not called when any check denies the call.

`PolicyDefinition` uses ordered typed rules with allow or deny effects. An
empty rule field matches any value. A matching deny wins over an allow. When a
policy set has no matching allow, its deny default applies. Inactive policies
are not executable. Rules can select an agent, principal, tenant, tool,
environment, risk tier, resource type, or resource ID. A rule can also require
approval.

Principal mappings, agent mappings, environment constraints, and policy rules
can only reduce the trusted authority in `ExecutionContext`. They cannot add a
tool, permission, approval, or risk budget. The runtime applies the same
ceiling after a custom broker returns, so a custom implementation cannot grant
authority above the context.

Each evaluation returns a `PolicyDecision`. The runtime stores that record in
`RunTrace.policy_decisions` and emits a redacted `policy_decision` trace and
audit event. Resource hooks receive application facts and proposed arguments;
their data is not authority and their result cannot expand the execution
context.

## Bounded execution controls

`AgentRuntime` records context partition counts, plan steps, tool results,
retry decisions, control interruptions, terminal state, and the final outcome.
The exported trace keeps event order and does not include raw input, provider
prompts, or tool output.

`CancellationToken` and a standard `threading.Event` provide cooperative
cancellation. A synchronous provider or handler that is already running may
continue in its worker after the runtime stops waiting. Application handlers
must use remote timeouts, cancellation-aware clients, and idempotency for
side-effecting work.

## Provider boundary

The runtime communicates with a provider through three typed operations:

| Operation | Request | Response | Runtime treatment |
| --- | --- | --- | --- |
| Interpretation | `InterpretationRequest` | `InterpretationResponse` | Store as non-authoritative request data. |
| Planning | `PlanningRequest` | `PlanningResponse` | Normalize tool calls, then validate exact tools and limits. |
| Composition | `CompositionRequest` | `CompositionResponse` | Verify evidence and choose the final `AgentOutcome` state. |

Adapters may return the canonical response models or provider-shaped mappings.
The normalization boundary converts common tool-call shapes into `PlanStep`
values and rejects invalid structured output. Provider SDK objects do not cross
the boundary.

The runtime records provider ID, provider version, model, request ID, latency,
retry count, and available token counts in `ProviderCallMetadata`. These
records are part of `RunTrace.provider_calls`; raw prompts, model output, tool
arguments, and credentials are not recorded there.

`DeterministicProvider` is the default test provider. The optional
`OpenAIProviderAdapter` translates the OpenAI Responses API into the same
contracts. The adapter can receive an injected client for tests, so the core
test suite does not require network access or an API key.

## Agent lifecycle

Lifecycle applies to each versioned agent record. A logical agent can have
several versions. A registry may keep more than one version active for
controlled rollout, but every run selects one exact version.

| State | Meaning | Allowed work |
| --- | --- | --- |
| `draft` | The definition is being prepared. Dependencies and policy are not yet accepted. | No execution. The owner may edit it. |
| `validated` | The definition and referenced versions pass schema, dependency, compatibility, and policy checks. | No execution until activation. The version is frozen for activation. |
| `active` | The version is approved for use. | New executions may start. |
| `suspended` | Use is stopped by an owner or governance decision. | No new execution or resume. An in-flight run stops at its next safe runtime boundary; a currently running external handler is not assumed interruptible. |
| `deprecated` | The version remains recorded but must not receive new calls. | No new execution. A replacement version is required. |
| `retired` | The version is permanently removed from service. | No new execution or resume. A replacement version is required. |

Allowed transitions are:

```text
draft -> validated -> active -> suspended -> active
   |         |          |          |
   +---------+----------+----------+----> retired
```

`validated -> draft` is allowed only before activation when the owner changes
the definition. `retired` is terminal. Activation, suspension, reactivation,
and retirement are auditable registry operations. Suspension and retirement do
not silently rewrite an existing trace.

## Versioning rules

Python distribution versions follow the [PEP 440 version specification](https://peps.python.org/pep-0440/).
Component versions use a PEP 440-compatible `MAJOR.MINOR.PATCH` release
segment. The compatibility meaning follows the [Semantic Versioning
specification](https://semver.org/). Pre-release and development versions use
PEP 440 forms such as `1.2.0rc1` and `1.2.0.dev1`.

| Object | Version rule |
| --- | --- |
| Python distribution | Use one monotonically increasing PEP 440 version. The current development version is `0.1.0.dev0`. |
| Agent | Major changes alter the goal, allowed behavior, risk, policy, or required integration. Additive capabilities are minor. Internal fixes are patch. |
| Tool | A change to input or output meaning, side effects, kind, risk, required permission, approval requirement, or idempotency contract is breaking and needs a new major version. Compatible additions are minor. Behavior-preserving fixes are patch. |
| Capability | Adding a compatible operation is minor. Removing an operation, changing its meaning, or changing its authority boundary is major. Metadata-only fixes are patch. |
| Policy | Every behavior change gets a new immutable version. A change that can alter an existing allow, deny, or approval result is major for dependent manifests. |
| Provider profile | A change that requires a new adapter contract or changes proposal interpretation is major. Compatible configuration additions are minor. Metadata or defect fixes are patch. |
| Runtime contract | Schemas use an explicit major identifier such as `agent-outcome.v1`. Add optional fields without changing field meaning within a major. Use `v2` for a breaking change. |
| Trace and replay | The schema version is part of every export. Consumers must declare supported versions. Never reuse a field for a new meaning. |

All active versions are immutable. A correction creates a new version. A
resolved manifest pins exact versions. A range can be used during deployment
resolution, but not during an in-flight execution. The run trace records the
selected versions and schema versions.

The project is in major version zero. The API baseline in `public-api.md` is
the intended integration surface, but compatibility guarantees become formal
at the first stable release.

## Runtime and consumer ownership

The runtime owns deterministic control flow, contract validation, exact
resolution, trust labels, permission and approval ordering, safety ceilings,
execution limits, final outcomes, and trace/audit schemas.

The consumer owns authentication, identity claims, tenant isolation, business
policy, resource authorization, tool handlers, business data, credentials,
approval authority, durable storage, memory content, deployment, and external
evaluation.

The runtime must not silently fill a missing consumer responsibility with a
model guess. A missing identity, authority, policy, handler, or approval
decision fails closed or returns an explicit incomplete outcome.

## Minimum public API

The baseline API is documented in `public-api.md`. It includes typed identity,
execution, plan, result, and outcome contracts; the `AgentRuntime` boundary;
the `ProviderAdapter` protocol and request/response models; typed tool
definitions and registry; the permission boundary; state and memory extension
points; and trace/replay contracts. Provider integration details are in
`providers.md`.

Provider SDK types, private helpers, sink storage details, and evaluation
graders are not public API.

## Package and quality baseline

The project uses a `src/` package layout and a `tests/` public-boundary test
layout. The package map and local commands are in `development.md`.

The required quality workflow is `.github/workflows/ci.yml`. It runs on pushes
and pull requests and checks formatting, linting, strict typing, tests, and
Python compilation on supported Python versions. The core package has no
provider API-key requirement.

## Architecture records and phase boundary

The following decisions are accepted for this baseline:

- `adr/0001-provider-neutral-adapter.md` - provider boundary;
- `adr/0002-application-policy-ownership.md` - policy and authority;
- `adr/0003-separate-versioned-registries.md` - registry design; and
- `adr/0004-versioned-trace-contracts.md` - trace and replay contracts; and
- `adr/0005-bounded-execution-controls.md` - run timeout, cancellation, and
  retry budget controls.

Phase 0B establishes the decisions and skeleton. Phases 1 and 2 implement the
core contracts, provider-neutral request/response boundary, deterministic fake,
optional OpenAI adapter, normalization, provider call policies, and provider
metadata traces. Phases 3 and 4 implement the typed tool and governance
boundaries. Phase 5 implements the bounded coordinator and run controls. Later
phases implement durable approval, complete registries and factory behavior,
delegation, background execution, cost controls, and production integrations.
Those features must use this baseline.
