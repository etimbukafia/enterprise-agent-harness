# ADR 0012: Observability, audit, and cost controls

Status: accepted

## Context

Every enterprise agent execution must be inspectable and measurable. The
runtime already emits structured trace and audit records. It now needs
attributable metrics, cost and execution budgets, configurable redaction, and
correlation that survives retries and resumptions. Observability is structured
runtime evidence, not logging strings.

## Decision

1. Audit and trace remain distinct. `AuditEvent` records governance- and
   security-relevant decisions. `TraceEvent` and `RunTrace` record diagnostic
   evidence. Audit is not a duplicate of every trace event.
2. `AuditSink` and `TraceSink` are pluggable storage boundaries with
   deterministic in-memory implementations. The runtime never depends on a
   specific storage vendor. A sink `append` failure is isolated and recorded
   by a separate `ObservabilityFailureReporter`; the reporter itself is best
   effort. A sink failure never aborts an execution, changes a governance
   decision, or causes an irreversible action to repeat.
3. Metrics are aggregated from recorded provider and tool calls and exported in
   `ExecutionMetrics` attached to `RunTrace`. They include execution, provider,
   and tool latency; input, output, and total tokens; provider and per-tool
   breakdowns; and estimated cost. Metrics are attributable to one execution,
   correlation, agent, agent version, and attempt. Approval resumption merges
   prior and resumed records before it exports metrics.
4. Cost is configurable. `CostModel` is an extension point. The deterministic
   `StaticTokenCostModel` uses per-1k-token rates and defaults to zero cost when
   no model is configured. The runtime does not hard-code unstable vendor
   pricing.
5. Execution budgets are trusted limits on `RuntimeConfig`:
   `max_total_tokens`, `max_cost`, and `max_tool_invocations`, in addition to
   the existing elapsed-time and retry budgets. Budget exhaustion produces a
   structured terminal `FAILED` outcome with `SafetyFlag.BUDGET_EXHAUSTED`, not
   an uncontrolled exception. A model or provider cannot raise its own budget.
6. Redaction is a `Redactor` extension point with a `DefaultRedactor`. It
   applies to exported trace and audit metadata only. Tool arguments are
   redacted via the existing tool boundary, and event payloads are exported only
   as a digest. Redaction never destroys the data the runtime needs to execute
   an approved action.
7. Correlation IDs flow from `ExecutionContext` into `RunTrace` and
   `AuditEvent`. The event, delegation, retry, resumption, approval, tool, and
   provider records all share one correlation root; no subsystem generates an
   unrelated correlation ID.
8. `RunTrace` remains the stable trace bundle and carries the explicit schema
   version. It is the Phase 14-compatible export. Evaluation policy is not added
   to the core runtime.

## Consequences

Every important runtime decision can be reconstructed from structured evidence,
and consumers can impose cost and execution budgets without changing governance
behavior. Observability failures do not silently change governance decisions.

## Rejected alternatives

- Hard-coding OpenTelemetry, Datadog, CloudWatch, Splunk, or any one vendor into
  the core contract would break provider neutrality.
- Using audit as a mirror of every trace event would blur governance evidence
  and diagnostic evidence.
- Re-running an irreversible action because a trace or audit sink failed would
  be unsafe.
- Allowing provider output to raise a budget would violate the authority model.
