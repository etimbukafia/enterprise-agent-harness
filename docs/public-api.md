# Public API baseline

Status: accepted baseline for Phase 2.

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
| `enterprise_agent_harness.CapabilityDefinition` | Versioned capability metadata. |
| `enterprise_agent_harness.PolicyDefinition` | Versioned declarative policy metadata. |
| `enterprise_agent_harness.PolicyRule` | One typed allow or deny policy rule. |
| `enterprise_agent_harness.ToolCall` | Canonical provider-neutral tool-call proposal. |
| `enterprise_agent_harness.ActionProposal` | Canonical proposal for a potentially side-effecting action. |
| `enterprise_agent_harness.ApprovalRequest` | Exact action request for an approval boundary. |
| `enterprise_agent_harness.ApprovalDecision` | Exact approval evidence for one action digest. |
| `enterprise_agent_harness.AgentPlan` | Provider proposal for bounded tool steps. |
| `enterprise_agent_harness.PlanStep` | One proposed tool call. |
| `enterprise_agent_harness.ToolResult` | Typed, untrusted tool result envelope. |
| `enterprise_agent_harness.OutcomeProposal` | Provider proposal for an outcome. |
| `enterprise_agent_harness.AgentOutcome` | Runtime-owned final outcome. |
| `enterprise_agent_harness.ToolKind` | Read, write, or action classification. |
| `enterprise_agent_harness.RiskLevel` | Declared risk classification. |
| `enterprise_agent_harness.OutcomeStatus` | Standard final outcome state. |
| `enterprise_agent_harness.ToolResultStatus` | Standard one-tool result state. |

## Runtime and provider layer

| Import | Purpose |
| --- | --- |
| `enterprise_agent_harness.AgentRuntime` | Execute one bounded governed run. |
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
| `enterprise_agent_harness.ToolRegistry` | Explicit versioned tool resolution. |
| `enterprise_agent_harness.ToolInvocationError` | Safe tool-boundary failure. |
| `enterprise_agent_harness.PermissionBroker` | Permission decision boundary. |
| `enterprise_agent_harness.DefaultPermissionBroker` | Deny-by-default baseline broker. |
| `enterprise_agent_harness.SafetyPolicy` | Deterministic safety decision boundary. |

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
successful provider operation. The trace does not contain raw provider
prompts or response content.

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
