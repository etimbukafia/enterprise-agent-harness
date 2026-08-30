# Public API baseline

Status: accepted baseline for Phase 6.

This document defines the smallest API that later phases must preserve. The
root package may re-export additional compatibility names during the 0.x
development period. Code that is not listed here is not a stable integration
surface.

## Contract layer

The following contracts are stable concepts. Later phases may add fields only
when the versioning rules in `architecture.md` allow it.

| Import | Purpose |
| --- | --- |
| `enterprise_agent_harness.AgentDefinition` | Declarative agent manifest with exact versioned references. |
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
| `enterprise_agent_harness.ToolKind` | Read, write, or action classification. |
| `enterprise_agent_harness.RiskLevel` | Declared risk classification. |
| `enterprise_agent_harness.OutcomeStatus` | Standard final outcome state. |
| `enterprise_agent_harness.ToolResultStatus` | Standard one-tool result state. |
| `enterprise_agent_harness.ToolExecutionRecord` | Redacted execution summary for one registry-managed handler call. |

## Runtime and provider layer

| Import | Purpose |
| --- | --- |
| `enterprise_agent_harness.AgentRuntime` | Execute one bounded governed run. |
| `enterprise_agent_harness.AgentRuntime.resume` | Resume one paused execution after exact approval evidence. |
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

## Tool and governance layer

| Import | Purpose |
| --- | --- |
| `enterprise_agent_harness.ToolDefinition` | Application-owned typed tool boundary. |
| `enterprise_agent_harness.ToolRegistry` | Versioned registration, lifecycle, resolution, and guarded execution. |
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

## State, memory, and evidence layer

The protocols are public extension points. They are imported from their
subpackages until the root export list is deliberately expanded.

| Import | Purpose |
| --- | --- |
| `enterprise_agent_harness.state.StateStore` | Versioned workflow-state storage boundary. |
| `enterprise_agent_harness.state.InMemoryStateStore` | Deterministic local state store. |
| `enterprise_agent_harness.memory.MemoryStrategy` | Optional memory boundary. |
| `enterprise_agent_harness.memory.BoundedMemory` | Bounded local memory strategy. |
| `enterprise_agent_harness.observability.AuditSink` | Audit-event storage boundary. |
| `enterprise_agent_harness.observability.ListAuditSink` | Deterministic audit sink. |
| `enterprise_agent_harness.observability.TraceSink` | Trace-event storage boundary. |
| `enterprise_agent_harness.observability.ListTraceSink` | Deterministic trace sink. |
| `enterprise_agent_harness.observability.TraceRecorder` | Redacted trace recorder. |

## Trace and replay layer

| Import | Purpose |
| --- | --- |
| `enterprise_agent_harness.RunTrace` | Exported run evidence. |
| `enterprise_agent_harness.TraceEvent` | One stable trace event. |
| `enterprise_agent_harness.ReplayRequest` | Replay input contract. |

`RunTrace.provider_calls` contains normalized provider metadata for each
successful provider operation. `RunTrace.policy_decisions` contains explicit
policy results, and `RunTrace.tool_executions` contains redacted handler
execution summaries. The trace does not contain raw provider prompts,
response content, idempotency keys, or tool output.

## Stability rules

- The runtime must accept and return the listed typed contracts without
  provider-specific types.
- A handler is reached only through `AgentRuntime` and its governance path.
- Provider adapters may propose data. They cannot call handlers or change
  authority.
- Exported trace and replay contracts carry an explicit schema version.
- Private helpers, concrete provider SDK objects, and sink storage details are
  not part of this baseline.
- A breaking change requires a new component or contract version, as defined
  in `architecture.md`.
- Install the optional `openai` extra only for the concrete OpenAI adapter:
  `python -m pip install -e ".[openai]"`.
