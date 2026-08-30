# ADR 0002: Keep business policy with the consuming application

- Status: accepted
- Date: 2026-08-30

## Context

The harness is used across domains. The application knows its identity
provider, tenant boundary, resource permissions, business rules, and approval
authority. A model cannot be the source of any of these decisions.

## Decision

The consuming application owns identity, business policy, resource-level
authorization data, tool handlers, credentials, and approval decisions. It
supplies the trusted principal and the execution authority for a run.

The runtime owns the execution safety ceiling. It validates proposals, calls a
permission or policy broker before each handler, applies deny-by-default
behavior, enforces approval requirements, and records the decision. A custom
broker may deny more work, but it cannot authorize a tool outside the trusted
execution allowlist or remove a runtime safety requirement.

Provider output, caller input, memory, and tool output are data. None of them
can modify identity, permissions, policy, risk, or approval requirements.

## Consequences

- The same runtime can serve different domains without embedding business
  rules.
- Integrations must provide an application policy and identity boundary.
- A policy decision is explicit and auditable.
- A missing application decision fails closed.
- The runtime cannot promise that a consumer policy is correct; it can only
  enforce the supplied boundary.

## Rejected alternatives

- Asking the provider to decide access would make untrusted output
  authoritative.
- Embedding one organization’s policy engine would violate domain neutrality.
- Giving a tool handler implicit authority would bypass the runtime boundary.
