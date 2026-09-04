# Production extension points

The harness provides contracts and local implementations. It does not provide
a production queue, database, identity service, or deployment system.

## Application-owned boundaries

| Boundary | Library contract | Consumer responsibility |
| --- | --- | --- |
| Provider | `ProviderAdapter` | Connect a model service. Keep provider output as proposal data. |
| Prompt and skill artifacts | `PromptDefinition`, `PromptRegistry`, `SkillDefinition`, and `SkillRegistry` | Publish immutable behavioral artifacts and exact tool references. Keep executable authority in each agent's explicit `tool_refs`. |
| Tool | `ToolDefinition` and `ToolRegistry` | Implement typed domain tools. Set risk, permission, approval, and idempotency fields. |
| Permission and policy | `PermissionBroker`, `PolicyDefinition`, and `ResourcePolicyHook` | Supply identity, resource facts, and business policy. |
| Approval | `ApprovalBroker` and `ApprovalPolicy` | Store requests, obtain reviewer decisions, and keep reviewer authority. |
| State | `StateStore` | Provide durable storage, ownership checks, version checks, and retention. |
| Background work | `BackgroundJobRunner`, `LeaseStore`, and `DeduplicationStore` | Connect a queue and durable, distributed lease and deduplication stores. |
| Audit | `AuditSink` and `RegistryAuditSink` | Store audit records and registry events with the required retention. |
| Trace | `TraceSink`, `CostModel`, `Redactor`, and `ObservabilityFailureReporter` | Store safe trace data, apply cost rules, protect sensitive fields, and monitor sink failures. |
| Evaluation | `TestCaseAdapter`, `MetricHook`, `HardGateHook`, and `RecordedReplayAdapter` | Own test data, metrics, thresholds, regression rules, and promotion decisions. |

Use `AgentFactory` to assemble registered components. Use `AgentComposer` for
bounded delegation. Use `AgentRuntime.execute_event` through
`BackgroundJobRunner` for event work.

## Local implementations

`InMemoryStateStore`, `InMemoryApprovalBroker`, `InMemoryLeaseStore`, and
`InMemoryDeduplicationStore` are useful for tests and local runs. They are not
distributed or durable. `SQLiteStateStore` provides a local durable state
option, but deployment still requires a retention and backup plan.

`ListAuditSink` and `ListTraceSink` keep records in process memory. They are
useful for examples. They do not provide audit retention or cross-process
collection.

## Production limits

The runtime still checks permissions, policy, approval, tool arguments, risk,
budgets, and lifecycle state when a consumer replaces a local implementation.
An extension must preserve the contract and fail closed when required evidence
is missing.

The library does not stop a synchronous handler that is already running. Use
remote timeouts, cancellation-aware clients, and idempotent side effects for
production tools.

The library does not provide tenant isolation, authentication, secret storage,
encryption, queue delivery, distributed locking, deployment, rollback, or
operational monitoring. The consuming application must provide these controls.

Evaluation remains outside the runtime. A metric or hard gate can inspect
exported evidence. It cannot change runtime permission, policy, approval, or
execution authority.
