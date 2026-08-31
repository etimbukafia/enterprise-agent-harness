# Enterprise Agent Harness: Phase-by-Phase Build Plan

## Goal

Build a reusable, provider-neutral enterprise agent runtime that can safely instantiate, execute, govern, compose, and observe AI agents across domains.

This project is intentionally independent of CX Autopilot or any other consuming product.

The project does not start from zero. It deliberately extracts and generalizes proven runtime and governance patterns from the source repository:

**Source repository:** https://github.com/etimbukafia/ai-assistant-harness

The source repository remains intact as a separate project. This project keeps its own history and product identity.

---

## Phase 0A: Source Harness Extraction and Generalization

Status: complete. See `PHASE_0A_SOURCE_AUDIT.md`,
`PHASE_0A_MIGRATION_MATRIX.md`, and `architecture.md` for the evidence and
scope decisions.

### Objective
Inspect https://github.com/etimbukafia/ai-assistant-harness, identify which concepts are reusable, and port only the parts that belong in a general enterprise-agent runtime.

This is a code and architecture fork. It is not a literal GitHub fork.

### Tasks
- [x] Fetch and inspect https://github.com/etimbukafia/ai-assistant-harness before implementation starts.
- [x] Audit every source module in that repository.
- [x] Classify each module or abstraction as `reuse mostly unchanged`, `generalize`, `redesign`, or `do not carry forward`.
- [x] Create and maintain a migration matrix from source modules to enterprise-harness destinations.
- [x] Extract the provider-neutral provider adapter pattern.
- [x] Extract and generalize `ToolDefinition` and typed tool-result contracts.
- [x] Extract the `PermissionBroker` pattern and preserve the rule that model output cannot grant authority.
- [x] Extract bounded execution-loop logic and remove assumptions that execution is only conversational.
- [x] Extract trusted versus untrusted context separation.
- [x] Extract audit-event and structured trace patterns.
- [x] Extract deterministic safety and prompt-injection handling where it generalizes.
- [x] Extract state/session patterns that remain useful outside conversational assistants.
- [x] Extract verification concepts that apply to general agent outcomes.
- [x] Extract provider conformance-test patterns.
- [x] Extract only the runtime-facing evaluation contracts needed for trace/replay interoperability.
- [x] Move full evaluation and improvement responsibility out of the harness boundary. Keep only runtime conformance and export contracts.
- [x] Remove the assumption that agents primarily answer questions.
- [x] Remove the assumption that tools are read-only.
- [x] Replace assistant response-state concepts with general agent outcome concepts where needed.
- [x] Preserve provenance in architecture notes so reused concepts remain traceable to the source repository.
- [x] Add parity tests for any behavior intentionally ported from the source repository.
- [x] Do not copy code that is specific to citation-heavy conversational assistants unless the concept remains useful in the broader runtime.

### Initial migration matrix

| Source module | Enterprise Agent Harness destination | Action |
| --- | --- | --- |
| `providers.py` | `providers/` | Generalize |
| `tools.py` | `tools/` | Generalize |
| `contracts.py` | `contracts.py` | Redesign around agent execution contracts |
| `permission.py` | `governance/permissions.py` | Generalize |
| `loops.py` | `runtime/execution.py` | Generalize beyond conversation turns |
| `audit.py` | `observability/audit.py` | Generalize |
| `rules.py` | `governance/safety.py` | Generalize |
| `context.py` | `runtime/context.py` | Reuse trusted/untrusted context concepts |
| `state.py` | `state/` | Generalize |
| `memory.py` | `memory/` | Redesign as optional memory strategies |
| `skills.py` | `capabilities/` | Evolve into capability contracts |
| `verification.py` | `verification/` | Generalize beyond citations and response sections |
| `recovery.py` | `runtime/outcomes.py` | Redesign around enterprise-agent outcomes |
| `harness.py` | `runtime/` | Redesign around general enterprise execution |
| `evaluation_contracts.py` | runtime trace/replay contracts | Generalize |
| `evals.py` | external evaluation system | Do not port full evaluation ownership |
| `__init__.py` | `__init__.py` | Redesign public exports |

### Exit criteria
- Every source module has an explicit migration decision.
- Ported behavior has parity tests.
- The new project has no runtime dependency on the source repository.
- https://github.com/etimbukafia/ai-assistant-harness remains intact.
- No conversational or read-only assumption survives unless it is intentional and documented.

---

## Phase 0B: Architecture Baseline

Status: complete. See `product-brief.md`, `architecture.md`,
`public-api.md`, `development.md`, and `adr/` for the baseline decisions and
quality workflow.

### Objective
Define the system boundaries, invariants, and package structure before implementation.

### Tasks
- [x] Write the product brief and explicit non-goals.
- [x] Define the trust model: model output is untrusted proposal data, application policy is authoritative.
- [x] Define the execution model for read tools, write tools, background actions, and approval-gated actions.
- [x] Define the agent lifecycle: draft, validated, active, suspended, retired.
- [x] Define versioning rules for agents, tools, capabilities, policies, and runtime contracts.
- [x] Decide which concepts are runtime-owned versus consumer-owned.
- [x] Define the minimum stable public API.
- [x] Create ADRs for provider neutrality, policy ownership, registry design, and trace contracts.
- [x] Establish `src/` package layout, test layout, linting, typing, and CI.

### Exit criteria
- Architecture document approved for the Phase 0B baseline.
- Core invariants are explicit.
- Package skeleton and quality workflow exist.

---

## Phase 1: Core Contracts and Type System

Status: complete. Evidence: `contracts.py`, `errors.py`, typed contract tests
in `tests/test_phase_1_contracts.py`, and the quality workflow.

### Objective
Create the typed contracts that every other module depends on.

### Tasks
- [x] Implement `AgentDefinition`.
- [x] Implement `AgentVersion` and immutable version identity.
- [x] Implement `CapabilityDefinition`.
- [x] Implement `ToolDefinition` with typed input/output schemas.
- [x] Implement `PolicyDefinition`.
- [x] Implement `PrincipalContext`.
- [x] Implement `ExecutionContext`.
- [x] Implement `ActionProposal`, `ToolCall`, and `ToolResult`.
- [x] Implement `ApprovalRequest` and `ApprovalDecision`.
- [x] Implement standardized `AgentOutcome` states.
- [x] Define error taxonomy for policy denial, validation failure, tool failure, provider failure, timeout, and cancellation.
- [x] Add serialization and validation tests.

### Exit criteria
- [x] All core entities can round-trip through JSON.
- [x] Invalid contracts fail deterministically.
- [x] No runtime logic depends on provider-specific types.

---

## Phase 2: Provider Abstraction

Status: complete. Evidence: `providers/`, provider-boundary tests in
`tests/test_phase_2_providers.py`, and the quality workflow.

### Objective
Keep models replaceable and prevent provider behavior from leaking into runtime policy.

### Tasks
- [x] Define a `ProviderAdapter` interface.
- [x] Define request/response contracts for interpretation, planning, and response composition.
- [x] Add a deterministic fake provider for tests.
- [x] Add one real provider adapter behind an optional dependency.
- [x] Normalize tool-call proposals across providers.
- [x] Add structured-output validation.
- [x] Add provider timeout and retry policy hooks.
- [x] Record token, latency, and model metadata in traces.
- [x] Add provider conformance tests.

### Exit criteria
- [x] Runtime tests pass with the fake provider only.
- [x] Swapping providers does not alter permission or policy behavior.

---

## Phase 3: Tool Runtime and Tool Registry

Status: complete. Evidence: `tools/definitions.py`, `tools/registry.py`,
`tests/test_phase_3_tools.py`, and the quality workflow.

### Objective
Create a safe, typed, reusable tool execution layer.

### Tasks
- [x] Implement in-memory `ToolRegistry`.
- [x] Support register, resolve, list, version, deprecate, and disable operations.
- [x] Validate tool arguments before execution.
- [x] Validate tool results after execution.
- [x] Add read/write/action risk classifications.
- [x] Add timeout support.
- [x] Add retry rules for explicitly retryable tools.
- [x] Add idempotency-key support for write tools.
- [x] Add tool dependency metadata.
- [x] Add tool ownership and tags.
- [x] Add tool execution tracing.
- [x] Add test fixtures for safe read, safe write, failing, slow, and destructive tools.

### Exit criteria
- [x] A tool cannot run with invalid arguments.
- [x] Write tools can be made idempotent.
- [x] Every invocation produces a structured trace record.

---

## Phase 4: Permission and Policy Engine

Status: complete. Evidence: `governance/permissions.py`, the policy and
permission contracts, `tests/test_phase_4_governance.py`, and the quality
workflow.

### Objective
Make deterministic governance the authority over proposed agent actions.

### Tasks
- [x] Implement `PermissionBroker`.
- [x] Support principal-based tool permissions.
- [x] Support agent-specific tool allowlists.
- [x] Support policy checks before every action.
- [x] Add deny-by-default behavior.
- [x] Add resource-level policy hooks.
- [x] Add environment constraints such as development, staging, and production.
- [x] Add risk-tier checks.
- [x] Add explicit policy decision records.
- [x] Prevent model/provider output from modifying permissions.
- [x] Add tests for privilege escalation attempts.

### Exit criteria
- [x] No tool call can bypass the broker.
- [x] Policy denials are auditable and deterministic.
- [x] Provider prompts cannot grant new authority.

---

## Phase 5: Bounded Agent Execution Runtime

Status: complete. Evidence: `runtime/execution.py`,
`runtime/control.py`, `tests/test_phase_5_runtime.py`, and the quality
workflow.

### Objective
Run a single enterprise agent safely through a bounded plan-and-act loop.

### Tasks
- [x] Implement the main `AgentRuntime` coordinator.
- [x] Compile trusted and untrusted context separately.
- [x] Add bounded step count.
- [x] Add execution timeout and cancellation.
- [x] Add tool-call validation and authorization before invocation.
- [x] Add explicit stop conditions.
- [x] Add retry budget controls.
- [x] Add deterministic terminal states.
- [x] Add structured execution traces.
- [x] Add safe handling for partial tool failure.
- [x] Add replayable deterministic test scenarios.

### Exit criteria
- [x] A single agent can complete a read/write workflow with policy enforcement.
- [x] Infinite loops are prevented.
- [x] Every run has a complete trace and terminal state.

---

## Phase 6: Human Approval Gates

Status: complete. Evidence: `governance/approvals.py`,
`runtime/execution.py`, `tests/test_phase_6_approval.py`, and the quality
workflow.

### Objective
Support sensitive actions without forcing all workflows to be fully autonomous.

### Tasks
- [x] Define approval policies by tool, action, risk, and environment.
- [x] Implement `ApprovalBroker`.
- [x] Support synchronous approval pauses.
- [x] Support resumable executions after approval.
- [x] Add approval expiry.
- [x] Add approve, reject, and request-change outcomes.
- [x] Preserve original action proposal and context for review.
- [x] Prevent agent modification of approval requirements.
- [x] Add audit events for all approval transitions.
- [x] Add tests for stale approvals and changed action payloads.

### Exit criteria
- [x] High-risk actions can pause and resume safely.
- [x] Approval applies only to the exact reviewed action.

---

## Phase 7: State, Memory, and Durable Execution

Status: complete. Evidence: `state/store.py`, `runtime/execution.py`,
`contracts.py`, `tests/test_phase_7_state.py`, and the quality workflow.

### Objective
Support enterprise workflows that outlive a single request.

### Tasks
- [x] Separate conversation/session memory from workflow state.
- [x] Define a `StateStore` interface.
- [x] Add in-memory implementation.
- [x] Add durable implementation, initially SQLite or PostgreSQL.
- [x] Support checkpoint and resume.
- [x] Add optimistic concurrency/version checks.
- [x] Add TTL and retention hooks.
- [x] Prevent untrusted retrieved text from becoming policy or authority.
- [x] Add state migration/versioning strategy.
- [x] Add crash/restart tests.

### Exit criteria
- [x] An execution can pause, process restart, and resume correctly.
- [x] State ownership and principal boundaries are enforced.

---

## Phase 8: Agent Registry and Capability Registry

Status: complete. Evidence: `registries.py`, `contracts.py`,
`tests/test_phase_8_registries.py`, and the quality workflow.

### Objective
Make agents and their capabilities discoverable and reusable.

### Tasks
- [x] Implement `AgentRegistry`.
- [x] Implement `CapabilityRegistry`.
- [x] Store goal, supported intents, tools, policies, risk level, language support, owner, version, status, and performance metadata.
- [x] Support activate, suspend, deprecate, and retire lifecycle operations.
- [x] Add capability search.
- [x] Add compatibility checks between agents, tools, and policies.
- [x] Add dependency graphs.
- [x] Add registry snapshots for deterministic planning.
- [x] Add read-only registry query API.
- [x] Add tests for duplicate, incompatible, and stale registrations.

### Exit criteria
- [x] A consumer can ask what capabilities already exist without inspecting code.
- [x] Registry state is versioned and auditable.

---

## Phase 9: Agent Factory

Status: complete. Evidence: `factory.py`, the declarative factory contracts in
`contracts.py`, and public-boundary tests in `tests/test_phase_9_factory.py`.

### Objective
Instantiate agents from reusable, approved pieces instead of writing new code for each agent.

### Tasks
- [x] Define declarative agent configuration format.
- [x] Build factory validation pipeline.
- [x] Resolve referenced tools, policies, provider, memory strategy, and runtime options.
- [x] Reject missing or incompatible dependencies.
- [x] Support reusable runtime profiles.
- [x] Support templates for common patterns: read-only analyst, action agent, approval-gated operator, router.
- [x] Generate immutable resolved manifests.
- [x] Register built agents into the Agent Registry.
- [x] Add dry-run mode.
- [x] Add factory conformance tests.

### Exit criteria
- [x] A new agent can be created by configuration plus registered components.
- [x] No arbitrary code generation is required for standard agents.

---

## Phase 10: Composition and Delegation

Status: complete. Evidence: `composition.py`, delegation and correlation
contracts in `contracts.py`, runtime trace/audit propagation, and public-boundary
tests in `tests/test_phase_10_composition.py`.

### Objective
Allow multiple agents and capabilities to cooperate without unrestricted peer authority.

### Tasks
- [x] Define delegation contracts.
- [x] Define parent-child execution context propagation.
- [x] Enforce delegated permission ceilings.
- [x] Add agent-to-agent invocation through the runtime, never direct bypass.
- [x] Add composition patterns: router, supervisor, specialist delegation, sequential workflow.
- [x] Add maximum delegation depth.
- [x] Add cycle detection.
- [x] Add shared trace correlation IDs.
- [x] Add tests for permission amplification attempts.

### Exit criteria
- [x] One agent can delegate to another safely.
- [x] Delegation cannot increase authority beyond the parent execution context.

---

## Phase 11: Event-Driven and Background Agents

Status: complete. Evidence: `background/`, `AgentRuntime.execute_event`,
`tests/test_phase_11_background.py`, and
`adr/0011-event-driven-background-execution.md`.

### Objective
Support agents triggered by events or scheduled work rather than only interactive calls.

### Tasks
- [x] Define event envelope and trigger contracts.
- [x] Add event-triggered execution entry point.
- [x] Add background job runner abstraction.
- [x] Add lease/lock semantics for duplicate event handling.
- [x] Add deduplication and idempotency.
- [x] Add dead-letter/failure handling hooks.
- [x] Add cancellation and retry policies.
- [x] Add event audit correlation.
- [x] Add tests for duplicate delivery and process restart.

### Exit criteria
- The same runtime can execute interactive and event-driven agents.
- Duplicate events do not cause duplicate irreversible actions.

---

## Phase 12: Observability, Audit, and Cost Controls

Status: complete. Evidence: `observability/metrics.py`,
`observability/redaction.py`, extended `RunTrace`/`AuditEvent`/`TraceEvent`
schemas, budget enforcement in `runtime/`, `tests/test_phase_12_observability.py`,
and `adr/0012-observability-audit-cost-controls.md`.

### Objective
Make every enterprise agent execution inspectable and measurable.

### Tasks
- [x] Define trace schema for provider calls, tool calls, policy decisions, approvals, delegation, state transitions, and final outcomes.
- [x] Implement pluggable `AuditSink`.
- [x] Implement pluggable `TraceSink`.
- [x] Add latency metrics.
- [x] Add token and provider cost metrics.
- [x] Add per-agent and per-tool usage metrics.
- [x] Add execution budget limits.
- [x] Add redaction hooks for sensitive fields.
- [x] Add correlation IDs.
- [x] Export a trace bundle suitable for external eval systems.

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

Status: complete. Evidence: `evaluation/`, `BuiltAgent.trace_for`,
`examples/external_evaluation.py`, `tests/test_phase_14_evaluation.py`, and the
quality workflow.

### Objective
Allow independent evaluation and improvement systems to consume harness behavior cleanly.

### Tasks
- [x] Define stable run trace export format.
- [x] Define agent manifest export format.
- [x] Define test-case adapter interface.
- [x] Define baseline and candidate identifiers.
- [x] Define metric and hard-gate hooks.
- [x] Add deterministic replay adapter.
- [x] Add example integration with an external evaluation package.
- [x] Keep evaluation policy outside the core runtime.

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

1. Phase 0A: Source Harness Extraction and Generalization
2. Phase 0B: Architecture Baseline
3. Phase 1: Core Contracts
4. Phase 2: Provider Abstraction
5. Phase 3: Tool Runtime
6. Phase 4: Permission and Policy Engine
7. Phase 5: Bounded Agent Runtime
8. Phase 6: Human Approval Gates
9. Phase 7: Durable Execution
10. Phase 8: Registries
11. Phase 9: Agent Factory
12. Phase 10: Composition and Delegation
13. Phase 11: Event-Driven and Background Agents
14. Phase 12: Observability and Audit

Then add safety hardening and the external evaluation contract.

## v0.1 milestone

A reasonable `v0.1` should prove the following:

- Proven source-runtime and governance concepts have been deliberately ported or rejected with documented migration decisions.
- A declarative agent can be instantiated by the factory.
- The agent is resolved from the Agent, Capability, and Tool registries.
- The runtime can execute bounded tool calls.
- Every tool call is permission-checked.
- A write action can require human approval.
- The execution emits structured traces and audit records.
- A provider can be swapped without changing governance behavior.
- An external evaluator can replay or score the exported trace.

That is the minimum coherent enterprise-agent harness. Everything after that expands execution modes and production depth.
