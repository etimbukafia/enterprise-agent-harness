# Public API baseline

Status: accepted baseline through Phase 14.

This document defines the smallest API that later phases must preserve. The
root package may re-export additional compatibility names during the 0.x
development period. Code that is not listed here is not a stable integration
surface.

## Contract layer

The following contracts are stable concepts. Later phases may add fields only
when the versioning rules in `architecture.md` allow it.

| Import | Purpose |
| --- | --- |
| `enterprise_agent_harness.AgentConfig` | Declarative factory input with exact component references. |
| `enterprise_agent_harness.AgentDefinition` | Declarative agent manifest with exact versioned references. |
| `enterprise_agent_harness.AgentTemplate` | Standard factory template: read-only analyst, action agent, approval-gated operator, or router. |
| `enterprise_agent_harness.AgentVersion` | Immutable logical agent identity and release version. |
| `enterprise_agent_harness.PrincipalContext` | Trusted principal, tenant, and session identity. |
| `enterprise_agent_harness.ExecutionContext` | Trusted authority and execution limits. |
| `enterprise_agent_harness.ResourceContext` | Optional application-supplied resource facts for policy checks. |
| `enterprise_agent_harness.CapabilityDefinition` | Versioned capability metadata. |
| `enterprise_agent_harness.PolicyDefinition` | Versioned declarative policy metadata. |
| `enterprise_agent_harness.PolicyRule` | One typed allow or deny policy rule. |
| `enterprise_agent_harness.PolicyDecision` | Explicit deterministic result of one policy evaluation. |
| `enterprise_agent_harness.ToolCall` | Canonical provider-neutral tool-call proposal. |
| `enterprise_agent_harness.ActionProposal` | Canonical proposal for a potentially side-effecting action. |
| `enterprise_agent_harness.ApprovalRequest` | Exact action request for an approval boundary. |
| `enterprise_agent_harness.ApprovalDecision` | Exact approval evidence for one action digest. |
| `enterprise_agent_harness.ApprovalDecisionStatus` | Approve, reject, request-change, or expired state. |
| `enterprise_agent_harness.ApprovalPolicy` | Versioned application approval policy. |
| `enterprise_agent_harness.ApprovalPolicyRule` | Tool, action, risk, and environment approval rule. |
| `enterprise_agent_harness.ApprovalPolicyDecision` | Deterministic approval-requirement result. |
| `enterprise_agent_harness.AgentPlan` | Provider proposal for bounded tool steps. |
| `enterprise_agent_harness.PlanStep` | One proposed tool call. |
| `enterprise_agent_harness.ToolResult` | Typed, untrusted tool result envelope. |
| `enterprise_agent_harness.OutcomeProposal` | Provider proposal for an outcome. |
| `enterprise_agent_harness.AgentOutcome` | Runtime-owned final outcome. |
| `enterprise_agent_harness.ExecutionCheckpoint` | Versioned, owner-bound continuation data for a paused execution. |
| `enterprise_agent_harness.RegistryDependency` | Exact dependency edge in a registry snapshot. |
| `enterprise_agent_harness.RegistrySnapshot` | Versioned, deterministic view of agent, capability, tool, policy, and dependency records, including the tool registry revision. |
| `enterprise_agent_harness.ResolvedAgentManifest` | Tamper-evident resolved factory snapshot containing exact agent dependencies and runtime options. |
| `enterprise_agent_harness.RuntimeProfile` | Reusable versioned runtime-limit profile. |
| `enterprise_agent_harness.DelegationRequest` / `DelegationResult` | Exact parent-authorized child invocation and auditable result. |
| `enterprise_agent_harness.DelegatedExecutionContext` | Child authority, identity, budget, depth, path, and correlation snapshot. |
| `enterprise_agent_harness.CompositionDefinition` | Versioned router, supervisor, specialist, or sequential composition definition. |
| `enterprise_agent_harness.CompositionStep` / `CompositionResult` | Exact child steps and aggregate composition result. |
| `enterprise_agent_harness.ToolKind` | Read, write, or action classification. |
| `enterprise_agent_harness.RiskLevel` | Declared risk classification. |
| `enterprise_agent_harness.OutcomeStatus` | Standard final outcome state. |
| `enterprise_agent_harness.ToolResultStatus` | Standard one-tool result state. |
| `enterprise_agent_harness.ToolExecutionRecord` | Redacted execution summary for one registry-managed handler call. |
| `enterprise_agent_harness.ExecutionMetrics` | Structured, attributable usage and cost evidence for one execution. |
| `enterprise_agent_harness.ProviderUsageMetric` | Per-provider/model usage and cost for one execution. |
| `enterprise_agent_harness.ToolUsageMetric` | Per-tool usage for one execution. |

`AgentDefinition` includes supported intents and languages, owner and
lifecycle metadata, exact capability/tool/policy references, risk level, and
optional performance metadata. Registry records are immutable by exact
identity and version; a correction is registered as a new version.

## Runtime and provider layer

| Import | Purpose |
| --- | --- |
| `enterprise_agent_harness.AgentRuntime` | Execute one bounded governed run. |
| `enterprise_agent_harness.AgentRuntime.resume` | Resume one paused execution after exact approval evidence. |
| `enterprise_agent_harness.AgentRuntime.execute_event` | Execute one governed event through the normal runtime path. |
| `enterprise_agent_harness.CancellationToken` | Request cooperative cancellation of a synchronous run. |
| `enterprise_agent_harness.ContextCompiler` | Compile trust-labelled provider context. |
| `enterprise_agent_harness.ProviderAdapter` | Provider-neutral proposal boundary. |
| `enterprise_agent_harness.InterpretationRequest` / `InterpretationResponse` | Typed interpretation operation boundary. |
| `enterprise_agent_harness.PlanningRequest` / `PlanningResponse` | Typed planning operation boundary. |
| `enterprise_agent_harness.CompositionRequest` / `CompositionResponse` | Typed response-composition boundary. |
| `enterprise_agent_harness.DeterministicProvider` | Offline provider for tests and examples. |
| `enterprise_agent_harness.OpenAIProviderAdapter` | Optional OpenAI Responses API adapter. |
| `enterprise_agent_harness.normalize_tool_calls` | Normalize provider tool-call shapes into `PlanStep` values. |
| `enterprise_agent_harness.DefaultProviderCallPolicy` | Finite-timeout, no-retry default provider policy. |
| `enterprise_agent_harness.AgentFactory` | Validate declarative config, resolve exact dependencies, register/activate agents, and construct governed runtimes. |
| `enterprise_agent_harness.BuiltAgent` | Tamper-evident resolved manifest plus an optional activated runtime; execution uses a private build-time authority snapshot. |
| `enterprise_agent_harness.ProviderRegistry` | Exact provider adapter registration and resolution. |
| `enterprise_agent_harness.RuntimeProfileRegistry` | Exact reusable runtime-profile registration and resolution. |
| `enterprise_agent_harness.AgentComposer` | Runtime-only delegation and router/supervisor/specialist/sequential composition. |

## Tool and governance layer

| Import | Purpose |
| --- | --- |
| `enterprise_agent_harness.ToolDefinition` | Application-owned typed tool boundary. |
| `enterprise_agent_harness.ToolRegistry` | Immutable exact-version registration, audited lifecycle and revision tracking, resolution, and guarded execution. |
| `enterprise_agent_harness.ToolRetryPolicy` | Explicit retry settings for a tool handler. |
| `enterprise_agent_harness.ToolInvocationError` | Safe tool-boundary failure. |
| `enterprise_agent_harness.PermissionBroker` | Permission decision boundary. |
| `enterprise_agent_harness.DefaultPermissionBroker` | Deny-by-default broker with policy, resource, environment, and risk checks. |
| `enterprise_agent_harness.ApprovalBroker` | Application boundary for exact approval requests and decisions. |
| `enterprise_agent_harness.InMemoryApprovalBroker` | Thread-safe local approval broker for tests and single-process use. |
| `enterprise_agent_harness.DeclarativeApprovalPolicyEngine` | Deterministic approval-policy evaluator. |
| `enterprise_agent_harness.DeclarativePolicyEngine` / `PolicyEngine` | Deterministic allow/deny policy evaluator. |
| `enterprise_agent_harness.EnvironmentConstraint` | Environment-specific tool and risk ceiling. |
| `enterprise_agent_harness.ResourcePolicyHook` | Application hook for resource-level policy decisions. |
| `enterprise_agent_harness.SafetyPolicy` | Deterministic safety decision boundary. |

## Registry layer

| Import | Purpose |
| --- | --- |
| `enterprise_agent_harness.AgentRegistry` | Register, validate, activate, suspend, deprecate, retire, resolve, search, and snapshot agent definitions. |
| `enterprise_agent_harness.CapabilityRegistry` | Register, validate, activate, suspend, deprecate, retire, resolve, search, and snapshot capability definitions. |
| `enterprise_agent_harness.RegistryAuditEvent` | Versioned audit record for registry mutations and snapshots. |
| `enterprise_agent_harness.RegistryAuditSink` | Application storage boundary for registry audit events. |
| `enterprise_agent_harness.ListRegistryAuditSink` | Thread-safe in-memory audit sink for local use and tests. |
| `enterprise_agent_harness.RegistryError` | Base registry lookup and lifecycle error. |
| `enterprise_agent_harness.DuplicateRegistrationError` | Exact identity/version is already registered. |
| `enterprise_agent_harness.StaleRegistrationError` | An immutable registered version was targeted for replacement. |
| `enterprise_agent_harness.IncompatibleRegistrationError` | A referenced tool, capability, or policy is missing, inactive, or exceeds the risk boundary. |
| `enterprise_agent_harness.RegistryLifecycleError` | A requested lifecycle transition is not allowed. |

Registry queries return deep copies and therefore cannot mutate registered
definitions. Exact tool versions cannot be replaced. Activation checks exact
dependency versions, active lifecycle states, and risk ceilings. A deprecated
tool cannot be activated again, and a retired tool is terminal. `snapshot()`
returns stable ordering and exact dependency edges for deterministic planning;
its ID, timestamp, combined revision, and tool registry revision are retained
as audit evidence.

An active agent can resolve only while its exact agent, capability, tool, and
policy dependencies are active. A validated agent may reference validated or
active dependencies. Active agent resolution repeats the dependency checks, so
later suspension takes effect for previously built runtimes.

`AgentFactory.validate()` is read-only. `build(..., dry_run=True)` resolves and
returns a manifest without registering or constructing a runtime. An active
build registers the exact definition and creates `BuiltAgent`; standard
templates reject incompatible authority shapes. An approval-gated operator must
have an executable approval gate on every write or action tool: either the tool
sets `requires_approval=True` or a matching active allow policy rule requires
approval. `approval_requirements` metadata alone is insufficient. Without an
approval service, the runtime returns `escalated` and does not call the
handler.

`BuiltAgent.execute()` checks the manifest digest before provider execution,
then uses the private build-time authority snapshot for agent identity, tool
IDs, exact tool versions, and risk ceiling. A modified nested manifest fails
closed. A factory-created runtime is bound to the exact built agent ID and
version and checks the live registry before every new execution and resume;
using `BuiltAgent.runtime` directly does not bypass that guard. Suspending the
agent or an exact dependency therefore stops the next operation.

`BuiltAgent.trace_for(execution_id)` returns completed trace evidence without
exposing private runtime data.

`AgentComposer.delegate()` requires an active factory-built child. It
intersects parent tool IDs and exact `tool_id@version` authority with the
child's trusted build-time authority, intersects requested permissions with parent grants, rejects
child risk above the parent ceiling, and bounds steps by both runtimes. It
passes the child through `AgentRuntime`; it never calls a handler or provider
directly. Child traces and audits retain the parent correlation ID and record
parent execution, delegation depth, and path. Cycles and depth overflow fail
closed. Approval evidence is not inherited by a child.

`AgentRuntime.execute` accepts `timeout_seconds` and a
`cancellation_event`. The event can be a standard `threading.Event` or a
`CancellationToken`. A run checks both controls before each provider call and
tool step. Provider and tool waits also stop at the run deadline. Python cannot
force-stop a synchronous handler that is already running, so application
handlers must use their own cancellation and idempotency controls.

`RuntimeConfig.execution_timeout_seconds` sets the default run timeout and
`RuntimeConfig.max_retries` sets the shared retry budget. The budget counts
retries across provider and tool calls. A value of zero disables retries.
`RuntimeConfig.approval_expiry_seconds` sets the default lifetime of a
pending approval request. An active approval policy rule can set a shorter
lifetime.

Pass an `ApprovalBroker` to `AgentRuntime` for approval-gated actions. The
runtime returns `escalated` with an exact `ApprovalRequest` when a decision is
not available. Call `broker.approve`, `broker.reject`, or
`broker.request_changes`, then call `AgentRuntime.resume` with the execution
ID. Resume checks the request ID, action digest, and expiry before a handler
can run, then continues the stored bounded plan from the paused step. The
provider cannot replace the reviewed action during resume. An approval never
grants authority to another tool or argument set.

## Error layer

| Import | Purpose |
| --- | --- |
| `enterprise_agent_harness.ErrorCode` | Stable machine-readable error categories. |
| `enterprise_agent_harness.ContractValidationError` | Invalid contract data. |
| `enterprise_agent_harness.PolicyDeniedError` | Deterministic policy denial. |
| `enterprise_agent_harness.ToolValidationError` | Invalid tool arguments or results. |
| `enterprise_agent_harness.ToolFailureError` | Application tool-handler failure. |
| `enterprise_agent_harness.ProviderError` | Provider call failure. |
| `enterprise_agent_harness.ProviderTimeoutError` | Provider call timeout with a retry hint. |
| `enterprise_agent_harness.ExecutionTimeoutError` / `ExecutionCancelledError` | Whole-execution timeout or cancellation categories. |
| `enterprise_agent_harness.RuntimeAuthorizationError` | A factory-created runtime is not authorized for its exact agent identity or current registry state. |
| `enterprise_agent_harness.FactoryError` and subclasses | Declarative factory validation, dependency, template, or manifest-authority failure. |
| `enterprise_agent_harness.DelegationError` and subclasses | Safe delegation or composition failure, including authority, cycle, and depth violations. |

## State, memory, and evidence layer

The protocols are public extension points. The state implementations and
retention errors are also re-exported from the package root for the common
durable-execution path.

| Import | Purpose |
| --- | --- |
| `enterprise_agent_harness.state.StateStore` | Versioned workflow-state storage boundary. |
| `enterprise_agent_harness.state.InMemoryStateStore` | Deterministic local state store. |
| `enterprise_agent_harness.state.SQLiteStateStore` | SQLite-backed state store with schema migrations, JSON data, owner checks, optimistic concurrency, and retention hooks. |
| `enterprise_agent_harness.state.StateRetentionHook` | Application retention callback evaluated by `purge_expired()`. |
| `enterprise_agent_harness.state.StateConflictError` | Optimistic version check failed. |
| `enterprise_agent_harness.state.StateOwnershipError` | A principal, tenant, or session boundary was violated. |
| `enterprise_agent_harness.state.StateSerializationError` | State data could not be encoded or decoded as JSON. |
| `enterprise_agent_harness.memory.MemoryStrategy` | Optional memory boundary. |
| `enterprise_agent_harness.memory.BoundedMemory` | Bounded local memory strategy. |
| `enterprise_agent_harness.observability.AuditSink` | Audit-event storage boundary. |
| `enterprise_agent_harness.observability.ListAuditSink` | Deterministic audit sink. |
| `enterprise_agent_harness.observability.ObservabilityFailureReporter` | Safe failure-reporting boundary for audit and trace sinks. |
| `enterprise_agent_harness.observability.ListObservabilityFailureReporter` | Deterministic in-memory observability failure reporter. |
| `enterprise_agent_harness.observability.TraceSink` | Trace-event storage boundary. |
| `enterprise_agent_harness.observability.ListTraceSink` | Deterministic trace sink. |
| `enterprise_agent_harness.observability.TraceRecorder` | Redacted trace recorder. |
| `enterprise_agent_harness.observability.CostModel` | Provider usage pricing boundary. |
| `enterprise_agent_harness.observability.StaticTokenCostModel` | Deterministic per-1k-token cost model. |
| `enterprise_agent_harness.observability.Redactor` | Exported metadata redaction boundary. |
| `enterprise_agent_harness.observability.DefaultRedactor` | Sensitive-key and value-length redaction. |

## Event-driven and background layer

| Import | Purpose |
| --- | --- |
| `enterprise_agent_harness.EventEnvelope` | One delivered event with stable identity and correlation data. |
| `enterprise_agent_harness.EventTrigger` | Routing metadata for an event type and source. |
| `enterprise_agent_harness.EventDisposition` | Terminal result of one background event. |
| `enterprise_agent_harness.FailureCategory` | Classification of a failed background attempt. |
| `enterprise_agent_harness.BackgroundJobRunner` | Dedup, lease, bounded retry, cancellation, and dead-letter coordination. |
| `enterprise_agent_harness.BackgroundJobRunner.resolve_pending` | Commit an approval-resumed result without re-running the event. |
| `enterprise_agent_harness.JobHandler` | Application boundary that executes one governed event attempt. |
| `enterprise_agent_harness.JobResult` | Structured terminal result of one background event. |
| `enterprise_agent_harness.BackgroundRetryPolicy` | Bounded retry policy for background handling. |
| `enterprise_agent_harness.LeaseStore` / `InMemoryLeaseStore` | Event-handling lease boundary and deterministic store. |
| `enterprise_agent_harness.Lease` | A time-bounded ownership record. |
| `enterprise_agent_harness.LeaseConflictError` / `LeaseExpiredError` | Lease boundary errors. |
| `enterprise_agent_harness.DeduplicationStore` / `InMemoryDeduplicationStore` | Event deduplication boundary and deterministic store. |
| `enterprise_agent_harness.DedupRecord` | One deduplication record for an event key. |
| `enterprise_agent_harness.DeadLetterSink` / `ListDeadLetterSink` | Dead-letter storage boundary and deterministic sink. |
| `enterprise_agent_harness.DeadLetterRecord` | Evidence for a terminal background failure. |

## Trace and replay layer

| Import | Purpose |
| --- | --- |
| `enterprise_agent_harness.RunTrace` | Exported run evidence. |
| `enterprise_agent_harness.TraceEvent` | One stable trace event. |
| `enterprise_agent_harness.ReplayRequest` | Replay input contract. |
| `enterprise_agent_harness.export_run_trace` | JSON-safe export of `RunTrace`. |
| `enterprise_agent_harness.export_agent_manifest` | JSON-safe export of `ResolvedAgentManifest`. |
| `enterprise_agent_harness.EvaluationExecutionInput` | Valid harness input from one external test case. |
| `enterprise_agent_harness.TestCaseAdapter` | External test-case to harness-input adapter protocol. |
| `enterprise_agent_harness.EvaluationSubject` | Baseline or candidate identity from an exact manifest. |
| `enterprise_agent_harness.EvaluationEvidence` | JSON-safe trace and manifest evidence for an external evaluator. |
| `enterprise_agent_harness.MetricHook` / `HardGateHook` | External metric and pass/fail policy protocols. |
| `enterprise_agent_harness.execute_test_case` | Run one adapted case through a factory-built agent. |
| `enterprise_agent_harness.RecordedReplayAdapter` | Offline recorded-evidence reconstruction. |

`RunTrace.provider_calls` contains normalized provider metadata for each
successful provider operation. `RunTrace.policy_decisions` contains explicit
policy results, and `RunTrace.tool_executions` contains redacted handler
execution summaries. The trace does not contain raw provider prompts,
response content, idempotency keys, or tool output.

`RunTrace` also exposes `correlation_id`, `parent_execution_id`,
`delegation_id`, `delegation_depth`, and `delegation_path` for reconstructing a
composed execution tree.

`export_run_trace` and `export_agent_manifest` return JSON-safe dictionaries.
Their source contracts have explicit schema versions. The manifest export keeps
the resolved agent, component, tool, policy, profile, and digest identities.

An external test system implements `TestCaseAdapter`. It returns an
`EvaluationExecutionInput`. `execute_test_case` then runs a `BuiltAgent` and
returns `EvaluationEvidence`. The external system owns `MetricHook`,
`HardGateHook`, thresholds, comparisons, and promotion decisions.

Use `EvaluationSubject.from_manifest(..., role="baseline" | "candidate")` to
identify compared builds. The manifest ID and digest distinguish exact builds.

`RecordedReplayAdapter` only validates and reconstructs exported trace data. It
does not invoke providers, tools, handlers, approvals, or state stores. It can
replay recorded evidence, but it cannot prove that a new live execution will
produce the same result.

`ExecutionCheckpoint` is stored in the workflow state's `data` by the runtime
when an approval-gated execution pauses. It contains the exact execution
context, remaining plan, reviewed request, prior typed evidence, and redacted
trace. A restarted runtime must call `resume(execution_id, principal=...)` so
the state store can enforce the owner boundary. The approval broker remains an
application-owned boundary; a process-restart deployment must provide a
durable broker or pass independently persisted, exact approval evidence.

## Stability rules

- The runtime must accept and return the listed typed contracts without
  provider-specific types.
- A handler is reached only through `AgentRuntime` and its governance path.
- A child handler is reached only through a factory-built `AgentRuntime` and
  receives no authority, budget, or approval evidence beyond the parent
  execution ceiling.
- Provider adapters may propose data. They cannot call handlers or change
  authority.
- Exported trace and replay contracts carry an explicit schema version.
- External evaluation is optional. Normal runtime execution needs no evaluation
  package or evaluation configuration.
- Private helpers, concrete provider SDK objects, and sink storage details are
  not part of this baseline.
- A breaking change requires a new component or contract version, as defined
  in `architecture.md`.
- Install the optional `openai` extra only for the concrete OpenAI adapter:
  `python -m pip install -e ".[openai]"`.
