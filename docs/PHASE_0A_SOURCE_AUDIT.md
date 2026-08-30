# Phase 0A source audit

## Scope

The audit used the source repository named in `BUILD_PLAN.md`:

<https://github.com/etimbukafia/ai-assistant-harness>

The audited source revision is:

- Commit: `9b38f4d37f00bc14432cc75d3f577ba9d1c0f5fa`
- Commit date: 2026-08-20
- Commit title: `Remove demo and tests from repository tracking`

The audit inspected all 17 tracked Python modules at that revision. It also
inspected the source README, architecture document, package metadata, data
cases, and the three test files from the initial source commit
`c069be4`. The initial tests define the parity behaviours listed below.

## Module findings

| Source module | Main abstraction | Reusable rule | Source-specific rule to remove or change |
| --- | --- | --- | --- |
| `__init__.py` | Public exports | Keep a small explicit public API. | Do not export assistant or evaluation ownership from the new package. |
| `audit.py` | `AuditLogger`, `AuditSink` | Write structured identity, action, and outcome events. | Use agent execution IDs. Redact raw input and tool output. |
| `context.py` | `ContextCompiler` | Label trusted and untrusted blocks. Keep required blocks in a budget. | Replace session conversation blocks with execution, workflow state, and generic input. |
| `contracts.py` | Pydantic boundary models | Reject extra fields and validate provider boundaries. | Replace `AssistantResponse`, `Citation`, conversation turns, and response sections with agent outcomes and evidence references. |
| `evals.py` | Case runner and scoring | Keep no runtime dependency on an evaluator. | Do not carry cases, graders, metrics, hard gates, or baseline comparison into the core runtime. |
| `evaluation_contracts.py` | Versioned evaluation models | Keep stable trace and replay data contracts. | Keep only runtime-facing trace and replay contracts. |
| `harness.py` | Turn coordinator | Keep explicit orchestration and application-owned decisions. | Redesign as `AgentRuntime` for one bounded execution. |
| `loops.py` | Bounded plan loop | Validate every planned call and stop on the first denied call. | Permit read, write, and action definitions. Require explicit authority and exact approval for sensitive actions. |
| `memory.py` | Bounded memory manager | Keep memory optional, bounded, principal-bound, and separate from evidence. | Store generic memory values, not a last question or response metadata. |
| `permission.py` | `PermissionBroker` | Check authority before the handler runs. | Check a trusted execution allowlist and approval digest. Never read authority from provider output. |
| `providers.py` | Provider methods | Keep a replaceable provider boundary and deterministic fake. | Return typed plan and outcome proposals. Do not expose handlers or credentials. |
| `recovery.py` | Recovery mapping | Map an unsafe state to one explicit next action. | Map generic outcome states. Do not use assistant response states. |
| `rules.py` | Injection and safety rules | Keep deterministic direct and indirect injection checks. | Apply rules to generic input and tool output. Keep policy outside the provider. |
| `skills.py` | `SkillRegistry`, `SkillDefinition` | Make configured capabilities visible to planning. | Generalize a skill definition into `CapabilityDefinition`; defer the full registry to Phase 8. |
| `state.py` | Session store | Enforce owner checks and safe copy boundaries. | Use versioned workflow state. Do not store conversation history or raw tool output. |
| `tools.py` | `ToolDefinition`, `ToolRegistry` | Validate arguments, invoke an application handler, and validate results. | Add identity, version, output schema, kind, risk, idempotency, and approval metadata. |
| `verification.py` | Grounding verifier | Check provider references against returned tool data. | Verify generic evidence references. Remove required response sections and citation rules. |

## Source abstractions

| Source abstraction | New abstraction | Decision |
| --- | --- | --- |
| `AssistantProvider` | `ProviderAdapter` | Generalize. The adapter returns proposals only. |
| `AssistantPlan` and `PlanStep` | `AgentPlan` and `PlanStep` | Generalize. Steps address any registered tool. |
| `SkillDefinition` | `CapabilityDefinition` | Generalize. A capability is not an answer format. |
| `ToolDefinition` | `ToolDefinition` | Generalize. Add typed output and risk metadata. |
| Read-only `ToolResult` | `ToolResult` | Generalize. Add normalized status and generic evidence references. |
| `ReadAssistantPermissionBroker` | `DefaultPermissionBroker` | Generalize. Use deny-by-default execution authority. |
| `ConversationState` | `ExecutionState` | Redesign. Keep ownership and version checks. |
| `MemoryItem` and `MemoryManager` | `MemoryItem` and `BoundedMemory` | Redesign. Keep memory optional and non-authoritative. |
| `AssistantResponse` and `ResponseStatus` | `AgentOutcome` and `OutcomeStatus` | Redesign. The runtime owns the final state. |
| `GroundingVerification` | `VerificationResult` | Generalize. Verify evidence references without citation sections. |
| `AuditEvent` | `AuditEvent` | Generalize. Bind events to an agent execution. |
| Evaluation case and report models | `RunTrace` and `ReplayRequest` | Reuse selectively. Keep only trace/replay interoperability. |

## Parity behaviours

The new tests preserve these source behaviours through public boundaries:

1. Context marks caller input and tool output as untrusted and drops optional
   blocks when the context budget is full.
2. Memory is bounded and rejects instruction-like values.
3. Duplicate tool identity and version pairs are rejected, and a plan can
   select an exact registered version.
4. A denied tool call never reaches its handler.
5. Audit metadata excludes raw input and evidence-like text.
6. A direct injection is refused before planning or tool execution.
7. Indirect injection remains data and cannot change identity or authority.
8. State access is bound to the principal and tenant.
9. Provider output cannot create authority, bypass the tool registry, or choose
   the final outcome state.
10. Invalid tool output and invalid provider evidence references cannot produce
    a completed outcome.

The source tests also included assistant-specific citation sections, follow-up
question routing, and read-only restrictions. Those behaviours are not copied.
They are either generalized in the new contracts or rejected as outside the
enterprise runtime boundary.

## Audit conclusion

The reusable core is a provider adapter, typed tool boundary, permission
broker, bounded execution loop, trust-labelled context compiler, deterministic
safety policy, principal-bound state and memory boundary, structured audit and
trace sinks, generic outcome verification, and trace/replay contracts.

The new package has no import or runtime dependency on `assistant_harness`.
The source repository remains a separate project.
