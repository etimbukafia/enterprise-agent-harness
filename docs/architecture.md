# Enterprise agent runtime architecture

Status: accepted baseline through Phase 15.

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
- state and memory strategies when they are used;
- supported intents and languages; and
- owner, lifecycle, and performance metadata.

`AgentRegistry` and `CapabilityRegistry` resolve exact, compatible versions
from application-owned records. `AgentFactory` assembles an active runtime
from those records and emits a resolved manifest that pins every dependency
version before an execution starts. `AgentComposer` can invoke only registered,
factory-built runtime versions.

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
12. A paused execution checkpoint is owner-bound, versioned, and resumed only
    through the same governed plan and authority checks.
13. Evaluation systems consume exported behavior. Evaluation policy does not
    control runtime authority.
14. Delegation derives child authority by intersection with the parent context;
    it cannot add tools, exact tool versions, permissions, risk, steps, or
    approval evidence.
15. Delegated executions retain the parent correlation ID, carry an explicit
    depth and identity path, and fail closed on depth overflow or cycles.
16. A factory-built runtime is bound to one exact agent version and checks the
    live agent and dependency lifecycle before every new execution and resume.

## Responsibility boundaries

| Boundary | Runtime owns | Consumer owns |
| --- | --- | --- |
| Identity | Validate the shape and carry the supplied principal. | Authenticate the principal and establish tenant membership. |
| Authority | Build and enforce the execution ceiling. | Supply business authorization, resource facts, and the initial allowlist. |
| Agent | Resolve a typed, compatible manifest and assemble a governed runtime. | Own agent intent, owner metadata, and deployment choice. |
| Provider | Define the adapter contract and validate proposals. | Supply the adapter, provider credentials, and provider profile. |
| Tool | Resolve versions, validate inputs and outputs, and gate invocation. | Supply handlers, data access, credentials, side-effect behavior, and business errors. |
| Policy | Invoke the policy boundary and enforce the runtime safety ceiling. | Define business policy, resource rules, environment rules, and policy decisions. |
| Approval | Preserve the exact action and enforce the approval gate. | Supply approvers, approval policy, review interface, and decision authority. |
| Context | Compile trust-labelled blocks and bound context size. | Supply input, domain data, and optional memory values. |
| State | Define ownership, version, resume, migration, and retention-hook contracts; persist trusted continuation data. | Choose durable storage, encryption, retention, backups, and deployment. |
| Memory | Provide an optional strategy boundary and safety labels. | Choose memory content, retrieval, retention, and deletion policy. |
| Registry | Define records, resolution, lifecycle checks, and compatibility checks. | Own registration, metadata, approval to activate, and operational storage. |
| Composition | Enforce parent-child ceilings, depth, cycle checks, and runtime routing. | Choose composition definitions, delegation reasons, and business workflow semantics. |
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

Factory construction happens before this execution path. The declarative
configuration is resolved against active exact registry records, provider and
runtime-profile registries, memory/state strategies, and application policy
boundaries. A dry run returns the manifest without registration or runtime
construction. An active build registers the exact agent definition and keeps
the manifest with the runtime.

### Composition and delegation

`AgentComposer` is the only Phase 10 peer-invocation boundary. A delegation
request identifies exact parent and child agent versions and is checked against
the live parent `ExecutionContext`. The child must be active and factory-built.
The composer intersects parent-authorized tool IDs and exact `tool_id@version`
references with the child manifest, intersects requested permissions with
parent grants, rejects a child risk above the parent ceiling, and limits child
steps to the smaller parent and child budgets. Approval digests are never
inherited; a child action must pass its own approval boundary.

The child is then called through `BuiltAgent.execute`, which routes into
`AgentRuntime`; the composer does not call a handler or provider directly. The
child receives the same principal and correlation ID plus a parent execution
ID, delegation ID, positive depth, and an identity path. The path rejects
cycles and the configured maximum depth rejects unbounded fan-out. Router and
specialist patterns select one child, supervisor fans out to all declared
children, and sequential workflows pass a completed child summary to the next
child while stopping on a non-completed outcome.

Trace and audit records for a child carry the shared correlation ID and
delegation metadata, so a consumer can reconstruct the parent-child run tree
without treating child authority as an independent root.

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
| Event-driven action | `AgentRuntime.execute_event` resolves event input, forces event correlation fields, and calls the same governed `execute` path. `BackgroundJobRunner` adds dedup, lease, bounded retry, cancellation, and dead-letter controls without bypassing the runtime. |
| Approval-gated action | The runtime creates an exact digest from the tool identity, tool version, and canonical arguments. The handler runs only after a valid, unexpired decision for that digest. |

Approval is a gate on one exact action. A provider cannot change the action
after approval. When an application supplies an `ApprovalBroker`, the runtime
stores the exact action and continuation checkpoint in owner-bound workflow
state. It returns `escalated` when no decision is available. A later `resume`
checks the request ID, action digest, and approval expiry before the handler
runs, then continues the stored bounded plan from the paused step. A rejection
returns `refused`. A request for changes returns `needs_input`.

`ApprovalPolicy` rules can add approval requirements by tool ID, action ID or
kind, risk level, and environment. A policy can reduce autonomy but cannot
remove a requirement declared by a tool or a trusted permission decision.
`InMemoryApprovalBroker` is suitable for deterministic tests and one process.
For process-restart recovery, the consumer must provide a durable approval
broker or pass independently persisted, exact approval evidence. The runtime's
SQLite checkpoint persists the continuation data but does not claim ownership
of the approval service or its reviewer identity.

## Workflow state and memory

`StateStore` is the owner-bound workflow-state boundary. The in-memory
implementation is useful for local runs; `SQLiteStateStore` provides a durable
schema-migrated store for one or more runtime instances using the same
database. State rows are keyed by tenant, session, agent, and state identity.
Reads and writes verify principal ownership, and writes may require the
expected version so a stale worker fails closed.

An approval pause stores an `ExecutionCheckpoint` in the state data. The
checkpoint includes the trusted execution identity, exact remaining plan,
reviewed action, prior typed evidence, and redacted trace. Resume hydration
requires an explicit matching `PrincipalContext`; a provider is never allowed
to replace checkpoint authority or the reviewed plan. State data is JSON and
uses an explicit schema version and migration table. TTL and retention hooks
are opt-in application controls, and state storage does not imply encryption,
backup, or cross-process job leasing.

Workflow state is not conversational memory. `MemoryStrategy` remains an
optional, separately owned source and its selected items are labelled
untrusted by `ContextCompiler`. Neither retrieved text nor memory can become
policy, identity, or authority.

## Agent and capability registries

`CapabilityRegistry` stores exact capability versions and exposes read-only
copies for lookup, version listing, metadata search, lifecycle changes,
compatibility checks against active tools, and versioned snapshots.
`AgentRegistry` stores exact agent versions and checks referenced capabilities,
tools, policies, dependency lifecycle, risk ceilings, and tool dependencies
before validation or activation. Both registries support activation,
suspension, deprecation, and retirement. Exact versions are immutable;
replacement means registering a new version.

An active agent may register or resolve only when every exact dependency is
active. A validated agent may reference validated or active dependencies. Exact
active agent resolution repeats the dependency checks, so suspending a
capability, tool, or other dependency stops previously built agents before
their next execution or resume.

Registry queries return copies so callers cannot mutate registry state through
the read API. Snapshots sort component records and dependency edges for
deterministic planning and include exact agent-to-capability, agent-to-tool,
agent-to-policy, capability-to-tool, and tool-to-tool dependency edges.
Mutation and snapshot events use `RegistryAuditEvent` and an application-owned
`RegistryAuditSink`; tool registration and lifecycle changes use the same
audit contract. Capability and agent snapshots include the current tool
registry revision. The combined revision and event history make registry state
auditable.

## Declarative agent factory

`AgentConfig` is the factory input. It names one exact agent version, provider
profile, optional runtime profile or direct limits, exact capabilities/tools/
policies, risk and approval requirements, and optional state and memory
strategies. `RuntimeProfileRegistry` and `ProviderRegistry` resolve immutable
exact records. Standard templates validate common authority shapes: a
read-only analyst cannot expose side-effecting tools, an action agent must
expose one, an approval-gated operator must declare side-effect and review
control, and a router is composition-oriented. For an approval-gated operator,
every write or action tool must declare `requires_approval=True` or be covered
by a matching active allow rule with `requires_approval=True`. The
`approval_requirements` list is metadata and does not satisfy this check by
itself.

`AgentFactory.validate()` performs dependency and compatibility resolution
without mutation. `build()` can dry-run, register the definition, activate it,
and construct `AgentRuntime` with the resolved components. `BuiltAgent` keeps
identity, tool versions, and risk bounded by a private build-time authority
snapshot. `ResolvedAgentManifest` is a tamper-evident public snapshot. The
factory recalculates its digest before execution and rejects a modified
manifest before a provider or tool runs. A caller cannot override the pinned
agent identity or grant a tool outside the manifest. The factory does not
generate arbitrary code. A created runtime is bound to the exact agent ID and
version and applies a live registry guard before both new executions and
resumes, including calls made through `BuiltAgent.runtime`. If an approval
service is absent, a required approval produces an escalated outcome and the
handler is not called.

## Tool runtime and registry

`ToolDefinition` is the application-owned typed tool boundary. It declares the
tool identity, version, input and output models, kind, risk, permissions,
approval requirement, ownership, tags, dependencies, allowed environments,
timeout, and explicit retry settings.

`ToolRegistry` owns in-memory registration and exact resolution. It supports
registration, lookup, listing, version lookup, deprecation, suspension, and
retirement. An exact tool ID and version can be registered only once. A
correction or behavior change requires a new version. A deprecated tool cannot
return to active service, and a retired tool is terminal. Only active versions
resolve for execution. The registry increments its monotonic revision and
emits an audit event for every registration or lifecycle change. A provider
receives a `ToolDescriptor`, which contains metadata and schemas but never a
handler.

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
| Approval policy | Every behavior change gets a new immutable version. A change that can alter a required-review result is major for dependent manifests. |
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

## External evaluation integration

Phase 14 extends the existing `evaluation` contract package. It exports the
versioned `RunTrace` and `ResolvedAgentManifest` as JSON-safe data. A consumer
maps an external case to `EvaluationExecutionInput` and runs a `BuiltAgent`.
The result is `EvaluationEvidence` with a baseline or candidate manifest
identity.

Metric and hard-gate hooks are protocols only. The runtime does not call them.
They cannot change permissions, policy, approval, or execution authority.

Recorded replay validates and reconstructs exported trace evidence. It does not
call providers, tools, handlers, approvals, or state stores. It is safe for
irreversible actions because it does not perform live execution.

## Developer examples

The `examples` package uses only public contracts. It shows governed execution,
approval resume, delegation, event handling, registry discovery, factory use,
and external evaluation. The examples use deterministic providers and local
stores. They do not define production deployment patterns.

`quickstart.md` shows the shortest factory path. `production-extension-points.md`
lists consumer-owned production boundaries. `architecture-diagrams.md` shows
the governed flows without adding a runtime component.

## Package and quality baseline

The project uses a `src/` package layout and a `tests/` public-boundary test
layout. The package map and local commands are in `development.md`.

The required quality workflow is `.github/workflows/ci.yml`. It runs on pushes
and pull requests and checks formatting, linting, strict typing, tests, and
Python compilation on supported Python versions. The core package has no
provider API-key requirement.

## Event-driven and background execution

`EventEnvelope` carries four distinct identities: `event_id` (one delivered
event), `trigger_id` (derived from event type and source), `correlation_id`
(one logical work item, stable across retries, resumptions, and delegations),
and `deduplication_key` (idempotency identity). `causation_id` records an
optional parent event or execution. The raw event payload is never exported;
only `payload_digest` reaches trace and audit records.

`AgentRuntime.execute_event` is the only event entry point. It resolves the
provider input from an extractor or a deterministic default, forces the
event-derived correlation fields, and calls the existing `execute` path. It
never bypasses permission, policy, approval, budget, state, trace, audit,
tool-validation, or delegation controls.

`BackgroundJobRunner` owns dedup lookup, lease acquisition, bounded retry,
cancellation, dead-letter disposition, and event audit correlation. It calls an
application `JobHandler` that routes through `AgentRuntime.execute_event`.
Each retry is a new `execution_id` that keeps the same `correlation_id`,
`event_id`, `trigger_id`, and an incremented `attempt`. Retries are bounded and
only cover retryable transient failures; a completed irreversible action is
never retried. Deduplication guards the whole event, while a write/action tool
still carries its own idempotency key inside the governed execution.

Lease and dedup semantics are explicit extension points (`LeaseStore`,
`DeduplicationStore`) with deterministic in-memory implementations for tests.
They are single-process and do not provide distributed-lock or durable-queue
guarantees; production adapters for queues, schedulers, locks, and durable dedup
storage are consumer boundaries.

## Observability, audit, and cost controls

Audit and trace stay distinct. `AuditEvent` records governance- and
security-relevant decisions; `TraceEvent` and `RunTrace` record diagnostic
evidence. Both sinks are pluggable, and a sink `append` failure is isolated so
it never aborts an execution or changes a governance decision. A separate
`ObservabilityFailureReporter` records safe sink-failure evidence. Its own
failure is also best effort.

`RunTrace` carries attributable `ExecutionMetrics` with execution, provider,
and tool latency; input, output, and total tokens; per-provider and per-tool
breakdowns; and estimated cost. `CostModel` is an extension point with a
zero-cost deterministic default (`StaticTokenCostModel`); the runtime does not
hard-code unstable vendor pricing. Approval resume merges prior and resumed
records before it exports metrics.

Execution budgets are trusted `RuntimeConfig` limits: `max_total_tokens`,
`max_cost`, and `max_tool_invocations`, in addition to the existing elapsed-time
and retry budgets. Budget exhaustion produces a structured terminal `FAILED`
outcome with `SafetyFlag.BUDGET_EXHAUSTED`; a model or provider cannot raise
its own budget.

Redaction is a `Redactor` extension point applied to exported trace and audit
metadata. Tool arguments redact through the existing tool boundary, and event
payloads export only as a digest. Redaction never destroys the data the runtime
needs to execute an approved action.

## Architecture records and phase boundary

The following decisions are accepted for this baseline:

- `adr/0001-provider-neutral-adapter.md` - provider boundary;
- `adr/0002-application-policy-ownership.md` - policy and authority;
- `adr/0003-separate-versioned-registries.md` - registry design; and
- `adr/0004-versioned-trace-contracts.md` - trace and replay contracts; and
- `adr/0005-bounded-execution-controls.md` - run timeout, cancellation, and
  retry budget controls; and
- `adr/0006-human-approval-gates.md` - exact approval requests, pause, and
  resume; and
- `adr/0007-durable-state-and-resume.md` - owner-bound durable checkpoints,
  optimistic concurrency, retention, and migrations; and
- `adr/0008-versioned-agent-capability-registries.md` - immutable registry
  records, compatibility checks, lifecycle, snapshots, and audit; and
- `adr/0009-declarative-agent-factory.md` - declarative exact-component
  assembly, runtime profiles, templates, manifests, registration, and dry run;
  and
- `adr/0010-bounded-composition-and-delegation.md` - parent-child authority
  ceilings, runtime-only delegation, composition patterns, depth, cycles, and
  correlation; and
- `adr/0011-event-driven-background-execution.md` - event envelope and
  correlation identities, `execute_event`, lease and dedup extension points,
  bounded retry, dead-letter, and event audit correlation; and
- `adr/0012-observability-audit-cost-controls.md` - audit versus trace
  responsibilities, pluggable sinks, metrics and cost model, execution budgets,
  redaction, correlation, and trace bundle export.

Phase 0B establishes the decisions and skeleton. Phases 1 and 2 implement the
core contracts, provider-neutral request/response boundary, deterministic fake,
optional OpenAI adapter, normalization, provider call policies, and provider
metadata traces. Phases 3 and 4 implement the typed tool and governance
boundaries. Phase 5 implements the bounded coordinator and run controls. Phase
6 implements application-owned approval policy, exact-digest pause and resume,
 expiry, and review outcomes. Phase 7 implements owner-bound durable workflow
 state, checkpoint hydration, and retention/version hooks. Phase 8 implements
 agent and capability discovery, lifecycle, compatibility validation, and
 auditable deterministic snapshots. Phase 9 implements declarative factory
 resolution, reusable runtime profiles, templates, manifests, registration,
 and dry runs. Phase 10 implements runtime-only delegation, composition
 patterns, authority ceilings, depth/cycle controls, and shared correlation.
 Phase 11 implements event-driven and background execution with dedup, lease,
 bounded retry, cancellation, and dead-letter controls on top of the same
 runtime. Phase 12 implements structured audit and trace sinks, attributable
 metrics and cost, execution budgets, redaction, and correlation. Phase 14
 exports stable trace and manifest evidence, test-case adaptation, subject
 identity, policy-neutral hooks, and offline recorded replay. Production
 integrations remain later phases and must use this baseline.
