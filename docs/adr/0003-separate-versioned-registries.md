# ADR 0003: Use separate versioned registries

- Status: accepted
- Date: 2026-08-30

## Context

Agents, capabilities, and tools have different owners and release lifecycles.
A tool is an executable application boundary. A capability describes a
reusable function. An agent combines capabilities, tools, policies, provider
profiles, and runtime limits.

The build plan also requires discovery, lifecycle control, compatibility
checks, and auditable registry records.

## Decision

Use separate registries for agents, capabilities, and tools. A policy or
provider profile can be a versioned referenced component without being merged
into the tool registry.

Every registry record has a stable identity and an immutable version. A
resolved agent manifest stores exact references to its dependencies. A run
uses the resolved versions captured in its execution context and trace.

Registries support the lifecycle states `draft`, `validated`, `active`,
`suspended`, and `retired`. A registry may keep more than one active version
for one identity. Version selection is explicit; an in-flight run never
silently changes to another version.

Registry operations and lifecycle changes produce auditable records. The
registry does not execute a tool or grant a principal authority.

## Consequences

- Consumers can discover and reuse a capability without inspecting source
  code.
- Each registry can enforce its own validation and ownership rules.
- Cross-registry compatibility checks are required at agent resolution.
- The runtime must record manifest and dependency versions for replay.
- A unified registry remains possible only after a later architecture review.

## Rejected alternatives

- One registry for every object would hide different ownership and lifecycle
  rules.
- Floating dependency resolution during execution would weaken replay and
  could change authority without a new deployment.
- A registry that contains handlers would become an unsafe execution router.
