# Phase 0A migration matrix

This matrix is the maintained decision record for the source extraction.
Allowed actions are `reuse mostly unchanged`, `generalize`, `redesign`, and
`do not carry forward`.

| Source path | Destination | Action | Ported behaviour | Main change |
| --- | --- | --- | --- | --- |
| `src/assistant_harness/__init__.py` | `src/enterprise_agent_harness/__init__.py` | redesign | Explicit public exports. | Export agent contracts only. |
| `src/assistant_harness/audit.py` | `observability/audit.py` | generalize | Sink protocol, in-memory sink, event logger, metadata filtering. | Bind events to agent and execution IDs. |
| `src/assistant_harness/context.py` | `runtime/context.py` | generalize | Trust labels, block priorities, required-block budget handling. | Compile policy, principal, execution, state, capability, memory, input, and tool output. |
| `src/assistant_harness/contracts.py` | `contracts.py` | redesign | Strict Pydantic contracts and aware timestamps. | Replace assistant contracts with generic plans, results, outcomes, and state. |
| `src/assistant_harness/evals.py` | external evaluation system | do not carry forward | No runtime ownership of evaluation. | Do not copy case scoring, metrics, hard gates, or baseline comparison. |
| `src/assistant_harness/evaluation_contracts.py` | `evaluation/contracts.py` | generalize | Versioned machine-readable run data. | Keep only `RunTrace`, `TraceEvent`, and `ReplayRequest`. |
| `src/assistant_harness/harness.py` | `runtime/execution.py` | redesign | Explicit orchestration and safe early exits. | Execute generic bounded workflows and return `AgentOutcome`. |
| `src/assistant_harness/loops.py` | `runtime/execution.py` | generalize | Bounded steps, pre-handler permission checks, call records. | Support all tool kinds, output validation, idempotency, and approval digests. |
| `src/assistant_harness/memory.py` | `memory/strategies.py` | redesign | Bounded principal-bound memory and injection rejection. | Remove question and response metadata assumptions. |
| `src/assistant_harness/permission.py` | `governance/permissions.py` | generalize | Broker protocol and deny path before handler execution. | Use trusted execution authority and exact action approval. |
| `src/assistant_harness/providers.py` | `providers/base.py`, `providers/deterministic.py` | generalize | Replaceable provider boundary and deterministic fake. | Provider sees descriptors, not handlers or authority controls. |
| `src/assistant_harness/recovery.py` | `runtime/execution.py` | redesign | Explicit recovery mapping. | Map generic outcome states to request, refusal, escalation, retry, or abort. |
| `src/assistant_harness/rules.py` | `governance/safety.py` | generalize | Direct and indirect injection patterns and deterministic thresholds. | Apply rules to generic execution results and risk levels. |
| `src/assistant_harness/skills.py` | `capabilities/__init__.py`, `contracts.py` | generalize | Configured capability metadata. | Use capability contracts; full discovery registry remains Phase 8. |
| `src/assistant_harness/state.py` | `state/store.py` | redesign | Copy isolation, owner checks, in-memory store. | Add workflow state versioning and optimistic concurrency. |
| `src/assistant_harness/tools.py` | `tools/definitions.py`, `tools/registry.py` | generalize | Explicit registration, argument validation, handler boundary. | Add typed output, stable version, risk, write/action, idempotency, and approval metadata. |
| `src/assistant_harness/verification.py` | `verification/outcomes.py` | generalize | Provider references must belong to tool returns. | Verify generic evidence IDs instead of citation sections. |

## Runtime-facing evaluation boundary

The source evaluation package contains case schemas, expected tool calls,
metrics, hard gates, reports, and baseline comparison. These concerns belong
to an external evaluation system. The new runtime exports only:

- `TraceEvent` for structured runtime decisions;
- `RunTrace` for a stable, redacted execution trace;
- `ReplayRequest` for caller-controlled deterministic replay inputs.

The runtime does not score a run or decide whether a candidate is promoted.

## Provenance

The source repository is the origin for the ported boundary patterns. The
implementation is a clean package under the new project name. It does not
copy the source package or import it at runtime. The source URL and audited
commit are recorded in `PHASE_0A_SOURCE_AUDIT.md`.
