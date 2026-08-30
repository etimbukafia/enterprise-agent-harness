# ADR 0008: Versioned agent and capability registries

Status: accepted

## Context

Reusable agents and capabilities need to be discoverable without requiring a
consumer to inspect implementation code. A registry also needs to prevent an
inactive, incompatible, or stale component from silently entering a runtime
manifest. The existing tool registry and declarative contracts provide the
authoritative tool and policy metadata that agent and capability records must
reference.

## Decision

1. `CapabilityRegistry` and `AgentRegistry` are separate public boundaries.
   They store exact identity/version records and return deep copies from query
   methods. `CapabilityDefinition` and `AgentDefinition` carry intent,
   language, owner, lifecycle, risk, and performance metadata; agents also
   carry exact capability, tool, and policy references.
2. Exact versions are immutable. Duplicate registration fails with
   `DuplicateRegistrationError`; an attempted replacement fails with
   `StaleRegistrationError`. A correction or compatible change is a new
   version.
3. Both registries support draft, validated, active, suspended, deprecated,
   and retired states where applicable. Activation validates referenced
   versions, active lifecycle, tool dependencies, policy availability, and
   risk ceilings. A registry never activates a record merely because its
   shape is valid.
4. Queries support exact lookup, unambiguous active resolution, deterministic
   listing/version order, and metadata search. Search is discovery data, not
   authority to execute a tool.
5. `RegistrySnapshot` contains the selected records and sorted exact
   dependency edges. Snapshots include agent-to-capability,
   agent-to-tool, agent-to-policy, capability-to-tool, and tool-to-tool
   relations where present. Snapshot IDs, revisions, timestamps, and mutation
   events are retained for audit.
6. `RegistryAuditSink` is an application storage extension point. The default
   `ListRegistryAuditSink` is for local use and tests. Registry snapshots are
   deterministic in record and edge ordering, while their generated identity
   and timestamp remain evidence fields.
7. The registry layer does not instantiate providers or handlers. Agent factory
   assembly remains a later phase and must consume exact registry results.

## Consequences

Consumers can find reusable components by stable metadata and obtain a
versioned planning snapshot without importing private implementation code.
Compatibility failures are explicit and auditable. Exact references make an
in-flight manifest stable even when a newer registry version is published.

The first implementation keeps registry records in process memory. Consumers
that need a shared control plane can implement a durable registry and audit
sink behind these public concepts while preserving immutable version and
lifecycle semantics.

## Rejected alternatives

- A single untyped catalog would blur agent, capability, tool, and policy
  ownership and weaken compatibility checks.
- Mutable records would make an already-resolved run change underneath the
  runtime.
- Provider introspection would make discovery provider-specific and would
  allow untrusted output to influence authority.
- Search results that expose internal mutable objects would let a caller alter
  trusted registry state through a read API.
