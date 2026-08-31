# ADR 0009: Declarative agent factory and resolved manifests

Status: accepted

## Context

An agent definition can reference tools, capabilities, policies, providers,
runtime limits, state, and memory, but a consumer should not have to assemble
those pieces with bespoke code for every agent. Assembly must not become a
second authority system or silently select a newer component version.

## Decision

1. `AgentConfig` is the declarative factory input. It identifies one exact
   agent version and exact capability, tool, and policy references, plus a
   provider profile, optional runtime profile or direct limits, risk,
   approval, state, memory, owner, and template metadata.
2. `ProviderRegistry` and `RuntimeProfileRegistry` resolve immutable exact
   `(identity, version)` pairs. Missing or inactive records fail closed.
3. `AgentFactory.validate()` resolves dependencies and runs the same public
   compatibility checks as `AgentRegistry` without mutating the registry.
   `build()` can return a dry-run manifest, or register, activate, and create
   an `AgentRuntime` from the resolved components.
4. Standard templates validate common shapes: a read-only analyst cannot
   expose a side-effecting tool; an action agent must expose one; an
   approval-gated operator must expose side-effect and review control; and a
   router is composition-oriented.
5. `ResolvedAgentManifest` is a frozen, versioned snapshot containing the
   source configuration, agent definition, exact dependencies, provider,
   runtime limits, strategy choices, template, and a stable content digest.
   `BuiltAgent` pins execution identity, tool versions, and risk to this
   manifest.
6. The factory never generates arbitrary code and never gives a provider
   handlers, credentials, permission authority, or approval authority.

## Consequences

Consumers get a repeatable construction boundary and can validate deployment
configuration without starting an agent. A manifest is evidence of the exact
assembly inputs and can be retained with deployment records. Consumers still
own component registration, authentication, business policy, storage,
secrets, and deployment approval.

The first implementation keeps provider and runtime-profile records in
process memory. A consumer can provide a durable control-plane implementation
later without changing the exact-reference rule.

## Rejected alternatives

- Generating Python or provider-specific code per agent would create an
  unrestricted execution and review surface.
- Resolving version ranges at execution time would make an in-flight run
  change when a registry publishes a newer version.
- Having the factory bypass registries would duplicate lifecycle and
  compatibility authority.
