# Enterprise Agent Harness: Phase-by-Phase Build Plan

## Goal

Build a reusable, provider-neutral enterprise agent runtime that can safely instantiate, execute, govern, compose, and observe AI agents across domains.

This project is intentionally independent of CX Autopilot or any other consuming product.

---

## Phase 0: Architecture Baseline

### Objective
Define the system boundaries, invariants, and package structure before implementation.

### Tasks
- [ ] Write the product brief and explicit non-goals.
- [ ] Define the trust model: model output is untrusted proposal data, application policy is authoritative.
- [ ] Define the execution model for read tools, write tools, background actions, and approval-gated actions.
- [ ] Define the agent lifecycle: draft, validated, active, suspended, retired.
- [ ] Define versioning rules for agents, tools, capabilities, policies, and runtime contracts.
- [ ] Decide which concepts are runtime-owned versus consumer-owned.
- [ ] Define the minimum stable public API.
- [ ] Create ADRs for provider neutrality, policy ownership, registry design, and trace contracts.
- [ ] Establish `src/` package layout, test layout, linting, typing, and CI.

### Exit criteria
- Architecture document approved.
- Core invariants are explicit.
- Package skeleton and quality workflow exist.

---

## Phase 1: Core Contracts and Type System

### Objective
Create the typed contracts that every other module depends on.

### Tasks
- [ ] Implement `AgentDefinition`.
- [ ] Implement `AgentVersion` and immutable version identity.
- [ ] Implement `CapabilityDefinition`.
- [ ] Implement `ToolDefinition` with typed input/output schemas.
- [ ] Implement `PolicyDefinition`.
- [ ] Implement `PrincipalContext`.
- [ ] Implement `ExecutionContext`.
- [ ] Implement `ActionProposal`, `ToolCall`, and `ToolResult`.
- [ ] Implement `ApprovalRequest` and `ApprovalDecision`.
- [ ] Implement standardized `AgentOutcome` states.
- [ ] Define error taxonomy for policy denial, validation failure, tool failure, provider failure, timeout, and cancellation.
- [ ] Add serialization and validation tests.

### Exit criteria
- All core entities can round-trip through JSON.
- Invalid contracts fail deterministically.
- No runtime logic depends on provider-specific types.

---

## Phase 2: Provider Abstraction

### Objective
Keep models replaceable and prevent provider behavior from leaking into runtime policy.

### Tasks
- [ ] Define a `ProviderAdapter` interface.
- [ ] Define request/response contracts for interpretation, planning, and response composition.
- [ ] Add a deterministic fake provider for tests.
- [ ] Add one real provider adapter behind an optional dependency.
- [ ] Normalize tool-call proposals across providers.
- [ ] Add structured-output validation.
- [ ] Add provider timeout and retry policy hooks.
- [ ] Record token, latency, and model metadata in traces.
- [ ] Add provider conformance tests.

### Exit criteria
- Runtime tests pass with the fake provider only.
- Swapping providers does not alter permission or policy behavior.

---

## Phase 3: Tool Runtime and Tool Registry

### Objective
Create a safe, typed, reusable tool execution layer.

### Tasks
- [ ] Implement in-memory `ToolRegistry`.
- [ ] Support register, resolve, list, version, deprecate, and disable operations.
- [ ] Validate tool arguments before execution.
- [ ] Validate tool results after execution.
- [ ] Add read/write/action risk classifications.
- [ ] Add timeout support.
- [ ] Add retry rules for explicitly retryable tools.
- [ ] Add idempotency-key support for write tools.
- [ ] Add tool dependency metadata.
- [ ] Add tool ownership and tags.
- [ ] Add tool execution tracing.
- [ ] Add test fixtures for safe read, safe write, failing, slow, and destructive tools.

### Exit criteria
- A tool cannot run with invalid arguments.
- Write tools can be made idempotent.
- Every invocation produces a structured trace record.

---

## Phase 4: Permission and Policy Engine

### Objective
Make deterministic governance the authority over proposed agent actions.

### Tasks
- [ ] Implement `PermissionBroker`.
- [ ] Support principal-based tool permissions.
- [ ] Support agent-specific tool allowlists.
- [ ] Support policy checks before every action.
- [ ] Add deny-by-default behavior.
- [ ] Add resource-level policy hooks.
- [ ] Add environment constraints such as development, staging, and production.
- [ ] Add risk-tier checks.
- [ ] Add explicit policy decision records.
- [ ] Prevent model/provider output from modifying permissions.
- [ ] Add tests for privilege escalation attempts.

### Exit criteria
- No tool call can bypass the broker.
- Policy denials are auditable and deterministic.
- Provider prompts cannot grant new authority.

---

## Phase 5: Bounded Agent Execution Runtime

### Objective
Run a single enterprise agent safely through a bounded plan-and-act loop.

### Tasks
- [ ] Implement the main `AgentRuntime` coordinator.
- [ ] Compile trusted and untrusted context separately.
- [ ] Add bounded step count.
- [ ] Add execution timeout and cancellation.
- [ ] Add tool-call validation and authorization before invocation.
- [ ] Add explicit stop conditions.
- [ ] Add retry budget controls.
- [ ] Add deterministic terminal states.
- [ ] Add structured execution traces.
- [ ] Add safe handling for partial tool failure.
- [ ] Add replayable deterministic test scenarios.

### Exit criteria
- A single agent can complete a read/write workflow with policy enforcement.
- Infinite loops are prevented.
- Every run has a complete trace and terminal state.

---

## Phase 6: Human Approval Gates

### Objective
Support sensitive actions without forcing all workflows to be fully autonomous.

### Tasks
- [ ] Define approval policies by tool, action, risk, and environment.
- [ ] Implement `ApprovalBroker`.
- [ ] Support synchronous approval pauses.
- [ ] Support resumable executions after approval.
- [ ] Add approval expiry.
- [ ] Add approve, reject, and request-change outcomes.
- [ ] Preserve original action proposal and context for review.
- [ ] Prevent agent modification of approval requirements.
- [ ] Add audit events for all approval transitions.
- [ ] Add tests for stale approvals and changed action payloads.

### Exit criteria
- High-risk actions can pause and resume safely.
- Approval applies only to the exact reviewed action.

---

## Phase 7: State, Memory, and Durable Execution

### Objective
Support enterprise workflows that outlive a single request.

### Tasks
- [ ] Separate conversation/session memory from workflow state.
- [ ] Define a `StateStore` interface.
- [ ] Add in-memory implementation.
- [ ] Add durable implementation, initially SQLite or PostgreSQL.
- [ ] Support checkpoint and resume.
- [ ] Add optimistic concurrency/version checks.
- [ ] Add TTL and retention hooks.
- [ ] Prevent untrusted retrieved text from becoming policy or authority.
- [ ] Add state migration/versioning strategy.
- [ ] Add crash/restart tests.

### Exit criteria
- An execution can pause, process restart, and resume correctly.
- State ownership and principal boundaries are enforced.

---

## Phase 8: Agent Registry and Capability Registry

### Objective
Make agents and their capabilities discoverable and reusable.

### Tasks
- [ ] Implement `AgentRegistry`.
- [ ] Implement `CapabilityRegistry`.
- [ ] Store goal, supported intents, tools, policies, risk level, language support, owner, version, status, and performance metadata.
- [ ] Support activate, suspend, deprecate, and retire lifecycle operations.
- [ ] Add capability search.
- [ ] Add compatibility checks between agents, tools, and policies.
- [ ] Add dependency graphs.
- [ ] Add registry snapshots for deterministic planning.
- [ ] Add read-only registry query API.
- [ ] Add tests for duplicate, incompatible, and stale registrations.

### Exit criteria
- A consumer can ask what capabilities already exist without inspecting code.
- Registry state is versioned and auditable.

---

## Phase 9: Agent Factory

### Objective
Instantiate agents from reusable, approved pieces instead of writing new code for each agent.

### Tasks
- [ ] Define declarative agent configuration format.
- [ ] Build factory validation pipeline.
- [ ] Resolve referenced tools, policies, provider, memory strategy, and runtime options.
- [ ] Reject missing or incompatible dependencies.
- [ ] Support reusable runtime profiles.
- [ ] Support templates for common patterns: read-only analyst, action agent, approval-gated operator, router.
- [ ] Generate immutable resolved manifests.
- [ ] Register built agents into the Agent Registry.
- [ ] Add dry-run mode.
- [ ] Add factory conformance tests.

### Exit criteria
- A new agent can be created by configuration plus registered components.
- No arbitrary code generation is required for standard agents.

---

## Phase 10: Composition and Delegation

### Objective
Allow multiple agents and capabilities to cooperate without unrestricted peer authority.

### Tasks
- [ ] Define delegation contracts.
- [ ] Define parent-child execution context propagation.
- [ ] Enforce delegated permission ceilings.
- [ ] Add agent-to-agent invocation through the runtime, never direct bypass.
- [ ] Add composition patterns: router, supervisor, specialist delegation, sequential workflow.
- [ ] Add maximum delegation depth.
- [ ] Add cycle detection.
- [ ] Add shared trace correlation IDs.
- [ ] Add tests for permission amplification attempts.

### Exit criteria
- One agent can delegate to another safely.
- Delegation cannot increase authority beyond the parent execution context.

---

## Phase 11: Event-Driven and Background Agents

### Objective
Support agents triggered by events or scheduled work rather than only interactive calls.

### Tasks
- [ ] Define event envelope and trigger contracts.
- [ ] Add event-triggered execution entry point.
- [ ] Add background job runner abstraction.
- [ ] Add lease/lock semantics for duplicate event handling.
- [ ] Add deduplication and idempotency.
- [ ] Add dead-letter/failure handling hooks.
- [ ] Add cancellation and retry policies.
- [ ] Add event audit correlation.
- [ ] Add tests for duplicate delivery and process restart.

### Exit criteria
- The same runtime can execute interactive and event-driven agents.
- Duplicate events do not cause duplicate irreversible actions.

---

## Phase 12: Observability, Audit, and Cost Controls

### Objective
Make every enterprise agent execution inspectable and measurable.

### Tasks
- [ ] Define trace schema for provider calls, tool calls, policy decisions, approvals, delegation, state transitions, and final outcomes.
- [ ] Implement pluggable `AuditSink`.
- [ ] Implement pluggable `TraceSink`.
- [ ] Add latency metrics.
- [ ] Add token and provider cost metrics.
- [ ] Add per-agent and per-tool usage metrics.
- [ ] Add execution budget limits.
- [ ] Add redaction hooks for sensitive fields.
- [ ] Add correlation IDs.
- [ ] Export a trace bundle suitable for external eval systems.

### Exit criteria
- Every important runtime decision can be reconstructed from structured evidence.
- Consumers can impose cost and execution budgets.

---

## Phase 13: Safety and Adversarial Hardening

### Objective
Make common agent failure and attack paths explicit and testable.

### Tasks
- [ ] Add direct prompt injection tests.
- [ ] Add indirect prompt injection tests from tool output.
- [ ] Add untrusted-data labeling.
- [ ] Add tool-output size limits.
- [ ] Add action argument integrity checks.
- [ ] Add SSRF/path traversal protections to example tools where applicable.
- [ ] Add secret redaction and secret-handling guidance.
- [ ] Add least-privilege defaults.
- [ ] Add approval requirements for destructive examples.
- [ ] Add adversarial regression suite.

### Exit criteria
- Known injection and privilege-escalation cases fail safely.
- Safety checks are deterministic where possible.

---

## Phase 14: External Evaluation Integration Contract

### Objective
Allow independent evaluation and improvement systems to consume harness behavior cleanly.

### Tasks
- [ ] Define stable run trace export format.
- [ ] Define agent manifest export format.
- [ ] Define test-case adapter interface.
- [ ] Define baseline and candidate identifiers.
- [ ] Define metric and hard-gate hooks.
- [ ] Add deterministic replay adapter.
- [ ] Add example integration with an external evaluation package.
- [ ] Keep evaluation policy outside the core runtime.

### Exit criteria
- An external lab can evaluate an agent without importing private runtime internals.
- Runtime remains usable without the evaluation package installed.

---

## Phase 15: Developer Experience and Reference Examples

### Objective
Make the harness understandable and adoptable without weakening its contracts.

### Tasks
- [ ] Write quickstart.
- [ ] Add a minimal read-only analyst example.
- [ ] Add a write-tool example with idempotency.
- [ ] Add an approval-gated action example.
- [ ] Add a router plus specialist composition example.
- [ ] Add an event-driven example.
- [ ] Add agent factory examples.
- [ ] Add registry query examples.
- [ ] Document production extension points.
- [ ] Add architecture diagrams.

### Exit criteria
- A developer can build and run a governed agent in under 15 minutes.
- Examples demonstrate the major runtime patterns.

---

## Phase 16: Productionization Boundary

### Objective
Clearly separate what the library provides from what a production platform must provide.

### Tasks
- [ ] Document durable store requirements.
- [ ] Document encryption and secrets requirements.
- [ ] Document tenant isolation requirements.
- [ ] Document authentication and identity-provider integration.
- [ ] Document distributed locking and queue requirements.
- [ ] Document audit retention and compliance considerations.
- [ ] Document deployment and rollback expectations.
- [ ] Document provider failover expectations.
- [ ] Document operational SLOs and monitoring expectations.
- [ ] Publish explicit non-goals for the core package.

### Exit criteria
- No portfolio/demo implementation is presented as production-complete.
- Production responsibilities are explicit and testable through extension interfaces.

---

## Recommended implementation order

For the first useful release, complete:

1. Phase 0: Architecture Baseline
2. Phase 1: Core Contracts
3. Phase 2: Provider Abstraction
4. Phase 3: Tool Runtime
5. Phase 4: Permission and Policy Engine
6. Phase 5: Bounded Agent Runtime
7. Phase 6: Human Approval Gates
8. Phase 8: Registries
9. Phase 9: Agent Factory
10. Phase 12: Observability and Audit
11. Phase 14: External Evaluation Contract

Then add durable execution, composition, background agents, and hardening.

## v0.1 milestone

A reasonable `v0.1` should prove the following:

- A declarative agent can be instantiated by the factory.
- The agent is resolved from the Agent, Capability, and Tool registries.
- The runtime can execute bounded tool calls.
- Every tool call is permission-checked.
- A write action can require human approval.
- The execution emits structured traces and audit records.
- A provider can be swapped without changing governance behavior.
- An external evaluator can replay or score the exported trace.

That is the minimum coherent enterprise-agent harness. Everything after that expands execution modes and production depth.
