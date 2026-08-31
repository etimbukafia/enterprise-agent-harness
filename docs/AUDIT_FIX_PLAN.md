# Phase 0A-10 Audit Fix Plan

## Status

Status: Workstreams 1 through 7 complete. Workstream 8 pending.

This plan corrects the contract issues found in the Phase 0A-10 audit. The
work must preserve the provider-neutral design and the deny-by-default safety
model.

## Goal

Make version identity, runtime authority, approval gates, registry state,
state versions, and delegation identity enforceable at runtime.

## Scope

This plan covers these eight issues:

1. Mutable exact tool versions and invalid tool lifecycle transitions.
2. Mutable nested data in resolved agent manifests.
3. Approval-gated templates that can run an action without approval.
4. Stale or bypassed registry lifecycle checks.
5. Missing tool registry revisions and audit events.
6. Approval decisions without an exact approval request identity.
7. New state records that start at a version other than zero.
8. Reused delegation identities and overwritten child execution data.

## Required Invariants

- An exact component version is immutable after registration.
- A deprecated component cannot return to active service.
- Runtime authority comes from trusted registry data.
- A modified resolved manifest cannot change runtime authority.
- Every side-effecting tool in an approval-gated agent has an executable
  approval rule.
- Every execution and resume operation checks current registry state.
- Every registry change updates its revision and creates an audit event.
- An approval decision identifies one pending approval request.
- A new state record starts at version zero.
- A delegation identity and a child execution identity cannot be reused.

## Workstream 1: Tool Registry Integrity

### Changes

- [x] Reject a registration when the exact tool ID and version already exist.
- [x] Remove the ability to replace an exact tool version.
- [x] Require a new semantic version for a handler, schema, risk, permission,
  or approval change.
- [x] Add an explicit tool lifecycle transition table.
- [x] Reject `DEPRECATED` to `ACTIVE` transitions.
- [x] Reject all transitions out of `RETIRED`.
- [x] Add a monotonic revision to `ToolRegistry`.
- [x] Add registry audit events for tool registration and lifecycle changes.
- [x] Put shared registry audit contracts in a neutral module. This prevents a
  circular import between tool and component registries.
- [x] Include the tool registry revision in capability and agent snapshots.

### Acceptance Criteria

- A second registration of the same exact tool version fails.
- A deprecated or retired tool cannot become active.
- Each visible tool registry change increments the registry revision.
- Each tool registration and lifecycle change creates an audit event.
- A capability or agent snapshot revision changes when its visible tools
  change.

## Workstream 2: Resolved Manifest Integrity

### Changes

- [x] Create a private trusted authority snapshot when the factory builds an
  agent.
- [x] Store tool references and other authority data in immutable collections.
- [x] Do not derive execution authority from a public manifest object.
- [x] Recalculate the manifest digest before execution.
- [x] Reject execution when the calculated digest differs from the stored
  digest.
- [x] Use tuples and immutable mappings for resolved manifest collections where
  the public contract permits them.
- [x] Document the manifest as deeply immutable or tamper-evident. Use the term
  that matches the final implementation.

### Acceptance Criteria

- A caller cannot change the effective tool version through a nested manifest
  reference.
- A modified manifest fails its integrity check before a provider or tool runs.
- Runtime permission checks use the trusted build-time authority snapshot.
- The manifest digest represents all fields that affect runtime authority.

## Workstream 3: Approval-Gated Agent Enforcement

### Changes

- [x] Treat `approval_requirements` as metadata unless the factory compiles it
  into an executable policy rule.
- [x] Do not use a nonempty metadata list as proof of an approval control.
- [x] Find every side-effecting tool in an `APPROVAL_GATED_OPERATOR` agent.
- [x] Require each side-effecting tool to set `requires_approval=True` or match
  a policy rule that requires approval.
- [x] Reject factory construction when one side-effecting tool has no approval
  gate.
- [x] Preserve deny-by-default behavior when an approval service is absent.

### Acceptance Criteria

- An approval-gated agent cannot build with an unprotected write or action
  tool.
- A protected action pauses before tool execution.
- The tool handler runs only after a valid approval decision.
- Approval metadata alone does not enable factory validation.

## Workstream 4: Current Lifecycle Enforcement

### Changes

- [x] Require all dependencies of an `ACTIVE` agent to be `ACTIVE`.
- [x] Permit a `VALIDATED` agent to use `VALIDATED` or `ACTIVE` dependencies.
- [x] Revalidate agent dependencies during exact agent resolution.
- [x] Add an execution guard inside `AgentRuntime`.
- [x] Run the guard before each new execution and each resume operation.
- [x] Bind a factory-created runtime to the exact built agent ID and version.
- [x] Apply the guard when a caller uses `BuiltAgent.runtime` directly.
- [x] Reject execution when the agent or one of its dependencies is not active.

### Acceptance Criteria

- An active agent cannot register against a merely validated dependency.
- Suspending a capability stops each dependent agent before its next execution
  or resume operation.
- Suspending an agent stops execution through both `BuiltAgent` and its runtime.
- A caller cannot use a factory-created runtime for a different agent identity.

## Workstream 5: Exact Approval Request Binding

### Changes

- [x] Make `ApprovalDecision.request_id` mandatory.
- [x] Require an exact match with the pending `ApprovalRequest.request_id`.
- [x] Keep action digest, principal, expiry, and decision-state checks.
- [x] Mark an approval request as consumed after a successful resume.
- [x] Reject decisions for completed, cancelled, expired, or different
  executions.

### Acceptance Criteria

- A decision without a request ID fails contract validation.
- A decision for a different request cannot resume an execution.
- A matching action digest cannot replace an exact request ID match.
- A consumed approval decision cannot run the action again.

## Workstream 6: State Creation Version Rules

### Changes

- [x] Require `state.version == 0` when a state record does not exist.
- [x] Permit only `expected_version=None` or `expected_version=0` during state
  creation.
- [x] Reject a new state with a nonzero version in the in-memory store.
- [x] Apply the same rule in the SQLite store.
- [x] Preserve compare-and-swap checks for existing state records.

### Acceptance Criteria

- Both stores reject a new state at version 1 or higher.
- Both stores reject an invalid expected version during creation.
- Both stores accept a valid version-zero creation.
- Existing state updates still require the correct expected version.

## Workstream 7: Delegation and Execution Identity

### Changes

- [x] Treat a delegation ID as single-use within its parent execution.
- [x] Generate a separate unique child execution ID for every delegation.
- [x] Generate a separate child state ID. Do not derive all child identity from
  caller-controlled input.
- [x] Reject duplicate execution IDs before trace or state data can be written.
- [x] Keep the parent execution ID and delegation ID as trace metadata.
- [x] Prevent a repeated delegation call from sharing state or replacing a
  previous trace.

### Acceptance Criteria

- Reuse of a delegation ID under the same parent fails.
- Two delegations cannot receive the same child execution or state identity.
- A duplicate execution cannot overwrite an earlier trace.
- Child authority remains equal to or less than parent authority.

## Regression Test Plan

Add tests through public boundaries. Each test must reproduce an audit probe or
protect a related invariant.

- [x] Tool registry rejects replacement of an exact version.
- [x] Tool registry rejects invalid lifecycle transitions.
- [x] Tool changes update revisions and audit events.
- [x] Agent and capability snapshots include tool revisions.
- [x] Nested manifest modification cannot change execution authority.
- [x] Manifest digest mismatch stops execution.
- [x] Approval-gated factory rejects an unprotected write tool.
- [x] Approval-gated action pauses before the handler and resumes after approval.
- [x] Approval-gated runtime fails closed without an approval service.
- [x] Active agent registration rejects a validated-only dependency.
- [x] Capability suspension stops a previously built agent.
- [x] Raw runtime execution stops after agent suspension.
- [x] A factory runtime rejects a different agent identity and suspended tools.
- [x] Approval decisions require and match a request ID.
- [x] Approval requests and decisions cannot be replayed.
- [x] In-memory state creation rejects nonzero versions.
- [x] SQLite state creation rejects nonzero versions.
- [x] Reused delegation IDs fail without trace or state replacement.
- [x] Unique delegations produce unique child execution and state IDs.

## Implementation Order

1. Add shared registry audit contracts. [x]
2. Correct tool registration, lifecycle, revision, and audit behavior. [x]
3. Add tool revisions to capability and agent snapshots. [x]
4. Correct agent registration and dynamic dependency checks. [x]
5. Add the runtime lifecycle guard and exact agent binding. [x]
6. Protect build-time authority and add manifest integrity checks. [x]
7. Enforce executable approval gates in the factory. [x]
8. Require exact approval request binding and consumption.
9. Correct state creation rules in both stores.
10. Correct delegation and execution identity rules.
11. Add regression tests for all audit probes.
12. Update architecture, ADR, and public API documents.
13. Run the complete quality suite.

## Documentation Updates

- [x] Update `architecture.md` with exact version and lifecycle rules.
- [x] Update `public-api.md` with manifest integrity and runtime guard behavior.
- [ ] Update the approval ADR with mandatory request identity and consumption.
- [x] Update the registry ADR with tool revisions and audit events.
- [x] Update the factory ADR with manifest integrity and trusted authority
  behavior.
- [ ] Update the delegation ADR with identity uniqueness rules.
- [ ] Update `BUILD_PLAN.md` with remediation status and evidence links.

## Completion Gates

The remediation is complete only when all these conditions are true:

- [ ] All audit regression tests pass.
- [ ] The complete test suite passes.
- [ ] Ruff formatting and lint checks pass.
- [ ] Strict mypy checks pass.
- [ ] Source compilation passes.
- [ ] Public documentation matches runtime behavior.
- [ ] No audit probe can reproduce its original result.
- [ ] The worktree contains no generated caches, local databases, secrets, or
  build output.

## Expected Evidence

Record these items when implementation is complete:

- The changed source files.
- The added or changed public-boundary tests.
- The final test count and result.
- The Ruff result.
- The strict mypy result.
- The source compilation result.
- The final audit probe results.

## Workstream 1 and 2 Evidence

- Regression tests: `tests/test_audit_workstreams_1_2.py`.
- Full test suite: 83 passed.
- Ruff format and lint: passed.
- Strict mypy for `src`: passed.
- Source compilation for `src` and `tests`: passed.
- Documentation: `architecture.md`, `public-api.md`, ADR 0008, and ADR 0009
  describe the new registry and manifest rules.

## Workstream 3 and 4 Evidence

- Regression tests: `tests/test_audit_workstreams_3_4.py` (9 passed).
- Approval enforcement: `AgentFactory` rejects every approval-gated build that
  leaves a write or action tool without `requires_approval=True` or a matching
  executable allow rule. `approval_requirements` remains metadata and is not
  treated as runtime approval authority.
- Approval runtime: a protected action pauses before the handler, resumes only
  after the broker approves the exact request, and remains escalated without an
  approval service.
- Lifecycle enforcement: active-agent registration and exact active resolution
  require active dependencies; validated agents accept validated or active
  dependencies. Factory runtimes revalidate the agent, capabilities, tools,
  and policies before new execution and resume.
- Runtime authority: factory-created runtimes are bound to their exact agent
  identity. Direct use through `BuiltAgent.runtime` cannot substitute another
  agent or bypass a suspended agent, capability, or tool.
- Full test suite: 92 passed.
- Ruff check: passed; Ruff format check: passed.
- Strict mypy for `src`: passed.
- Source compilation for `src` and `tests`: passed.
- Diff hygiene: `git diff --check` passed. Generated caches remain ignored by
  `.gitignore` and are not tracked.
- Documentation: `architecture.md`, `public-api.md`, ADR 0008, and ADR 0009
  describe approval-gate proof, live lifecycle revalidation, and runtime
  identity binding.

### Design evidence

The runtime already derives approval from trusted tool metadata and executable
policy decisions, while no runtime path consumes `approval_requirements`; the
factory therefore rejects metadata-only approval claims rather than inventing a
second approval authority. Lifecycle enforcement delegates to
`AgentRegistry.resolve()` so registration, exact resolution, and factory
runtime entry points use one current dependency check.
