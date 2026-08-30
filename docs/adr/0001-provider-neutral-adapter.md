# ADR 0001: Use a provider-neutral adapter

- Status: accepted
- Date: 2026-08-30

## Context

The runtime must support different model providers. Provider SDKs use
different request formats, tool-call formats, and response formats. Provider
features must not become permission or policy rules.

## Decision

The runtime uses a small `ProviderAdapter` boundary. An adapter receives
typed, trust-labelled context and versioned capability and tool metadata. It
returns an `AgentPlan` or an `OutcomeProposal`.

The runtime validates both proposals. The adapter does not receive a tool
handler. It cannot execute tools, grant permissions, change approval rules,
change identity, or choose the final `OutcomeStatus`.

The adapter may expose provider-specific options behind its own implementation
boundary. The core contracts do not contain provider SDK types.

## Consequences

- The runtime can use a deterministic provider for tests.
- Provider replacement does not change the governance path.
- Adapters must translate provider-specific failures and structured output into
  runtime contracts.
- Provider-specific quality and cost behavior remains visible in trace
  metadata but does not control authority.

## Rejected alternatives

- Calling a provider SDK from `AgentRuntime` would couple policy to one SDK.
- Letting a provider return executable callbacks would create an arbitrary
  code and authority channel.
- Defining one provider-specific tool schema would make replacement costly.
