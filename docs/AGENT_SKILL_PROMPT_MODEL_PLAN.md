# Enterprise Agent Harness Agent, Skill, Prompt, and Tool Plan

Status: proposed implementation plan

## 1. Goal

Make agent structure explicit and versioned.

The harness must distinguish four different artifacts:

```text
Agent
Skill
Prompt
Tool
```

Each artifact has a different purpose.

```text
Agent
  -> deployable governed actor

Skill
  -> reusable job competence

Prompt
  -> versioned behavioral instructions

Tool
  -> atomic executable operation
```

Policy remains a separate authority boundary.

The target agent graph is:

```text
Agent
  -> Prompt
  -> Skills
       -> Tools
  -> Tools
  -> Policies
  -> Provider profile
  -> Runtime profile
  -> State or memory strategy
```

The harness must keep these artifacts separate in contracts, registries, resolved manifests, and evidence.

This plan changes only Enterprise Agent Harness.

---

## 2. Core semantics

### Agent

An agent is a versioned deployable governed actor.

An agent composes approved runtime artifacts.

An agent can reference:

- one exact prompt version;
- zero or more exact skill versions;
- exact tool versions;
- exact policy versions;
- provider and runtime profiles;
- state or memory strategy;
- risk and lifecycle metadata.

An agent does not gain authority from prompt text or skill metadata.

### Skill

A skill is a reusable ability to perform a class of work.

Examples include:

```text
refund_resolution
incident_triage
contract_review
invoice_reconciliation
```

A skill can depend on one or more tools.

A skill can be reused by more than one agent.

A skill is not a tool.

A skill does not execute code by itself.

A skill does not grant permission to use a tool.

### Prompt

A prompt is a versioned behavioral instruction artifact.

It can define:

- role instructions;
- operating rules;
- response constraints;
- task guidance;
- language guidance.

A prompt cannot grant permissions.

A prompt cannot change policy.

A prompt cannot change approval requirements.

A prompt cannot expand the tool authority of an agent.

### Tool

A tool is an atomic executable operation.

It has:

- stable identity;
- version;
- typed input;
- typed output;
- risk classification;
- ownership metadata;
- permission requirements;
- retry and timeout behavior;
- approval requirements where applicable.

Tool execution remains governed by the existing runtime, permission, policy, and approval boundaries.

### Policy

Policy remains a deterministic authority boundary.

Do not merge policy with prompts, skills, or tools.

---

# Phase 0 - Architecture decision and migration baseline

## Goal

Freeze the new artifact semantics before changing public contracts.

## Tasks

- [ ] Add an ADR for the agent, skill, prompt, and tool model.
- [ ] Define `Agent`, `Skill`, `Prompt`, `Tool`, and `Policy` in one canonical document.
- [ ] Record that `Skill` replaces the existing `Capability` term.
- [ ] Record that the migration is forward-only during 0.x development.
- [ ] Record that no permanent `Capability` compatibility layer will remain.
- [ ] Record that prompt content does not grant runtime authority.
- [ ] Record that skill metadata does not grant runtime authority.
- [ ] Record that exact versions remain immutable.
- [ ] Create a migration matrix for public types, fields, registry names, snapshot edges, docs, tests, and examples.

## Required migration map

```text
CapabilityDefinition -> SkillDefinition
CapabilityRegistry -> SkillRegistry
capability_refs -> skill_refs
agent-to-capability -> agent-to-skill
capability-to-tool -> skill-to-tool
```

Do not keep two permanent vocabularies for the same concept.

## Exit criteria

The new semantics and migration path are explicit before implementation starts.

---

# Phase 1 - Skill contracts

## Goal

Replace the existing capability contract with a first-class skill contract.

## SkillDefinition

Create a typed immutable versioned contract.

Suggested fields:

```text
skill_id
version
name
description
supported_intents
required_tool_refs
optional_tool_refs
owner
lifecycle
risk_level
metadata
```

Use only fields that protect a real runtime or discovery boundary.

Do not add speculative workflow DSL fields.

## Required and optional tools

Distinguish required and optional tool dependencies only if the distinction is enforced.

A required tool means the skill is not valid for activation without that exact dependency.

An optional tool can enrich a skill but cannot be required for its base validity.

## Tasks

- [ ] Add `SkillDefinition`.
- [ ] Replace `CapabilityDefinition` usage.
- [ ] Update serialization and validation.
- [ ] Update root-package exports.
- [ ] Add behavior tests for valid and invalid skill contracts.
- [ ] Remove obsolete capability contract code after migration.

## Exit criteria

The public contract has one clear skill concept and no duplicate capability abstraction.

---

# Phase 2 - Skill registry

## Goal

Provide immutable versioned skill discovery and lifecycle management.

## Registry behavior

`SkillRegistry` must preserve the current registry principles:

- exact versions are immutable;
- duplicate exact registration fails;
- lifecycle changes are explicit;
- active resolution checks exact dependencies;
- search is discovery, not execution authority;
- reads return safe copies;
- audit events record mutations and snapshots.

## Tasks

- [ ] Replace `CapabilityRegistry` with `SkillRegistry`.
- [ ] Support register, validate, activate, suspend, deprecate, retire, resolve, list, search, and snapshot behavior.
- [ ] Validate required exact tool dependencies.
- [ ] Preserve deterministic listing and snapshot ordering.
- [ ] Update registry audit events.
- [ ] Add public-boundary tests for lifecycle and dependency behavior.

## Exit criteria

Consumers can discover and resolve reusable skills without inspecting application code.

---

# Phase 3 - Prompt contract

## Goal

Make behavioral instructions a first-class immutable versioned artifact.

## PromptDefinition

Add a small typed contract.

Suggested fields:

```text
prompt_id
version
purpose
instructions
owner
lifecycle
metadata
```

Add supported languages only if the existing agent contract requires the information at this boundary.

Do not build:

- a prompt DSL;
- prompt inheritance;
- prompt fragments;
- arbitrary templating;
- provider-specific prompt types;
- a prompt marketplace.

The first version should represent one complete versioned instruction artifact.

## Safety rules

Prompt content is untrusted instruction data with respect to authority.

A prompt cannot:

- add tool permissions;
- add policy rules;
- increase risk ceilings;
- remove approval gates;
- change principal identity;
- change tenant identity.

## Tasks

- [ ] Add `PromptDefinition`.
- [ ] Add validation for identity, version, lifecycle, and instruction content.
- [ ] Export the contract through the public package API.
- [ ] Add deterministic serialization tests.
- [ ] Add tests that show prompt content cannot alter authority contracts.

## Exit criteria

Prompt behavior can change by exact version without hiding the change inside an agent definition.

---

# Phase 4 - Prompt registry

## Goal

Provide immutable prompt discovery, lifecycle, and snapshot support.

## Tasks

- [ ] Add `PromptRegistry`.
- [ ] Use the same immutable exact-version rules as other registries.
- [ ] Support lifecycle operations.
- [ ] Support deterministic list and metadata search.
- [ ] Support exact resolution.
- [ ] Add registry audit events.
- [ ] Add prompt records to registry snapshots where applicable.
- [ ] Add public-boundary tests.

## Important rule

Prompt search is discovery only.

Selecting a prompt does not create an executable agent.

## Exit criteria

Prompts are independently discoverable and versioned without becoming an authority boundary.

---

# Phase 5 - Generic component references

## Goal

Give external systems one typed way to identify exact harness artifacts.

## ComponentType

Add a small enum:

```text
AGENT
PROMPT
SKILL
TOOL
POLICY
```

## ComponentReference

Suggested fields:

```text
component_type
component_id
version
```

Use this only where a generic component reference is genuinely useful.

Do not replace stronger typed references inside runtime-critical contracts when a typed field is clearer.

## Tasks

- [ ] Add `ComponentType`.
- [ ] Add immutable `ComponentReference`.
- [ ] Export both through the public API.
- [ ] Use the reference in snapshot, audit, or external integration boundaries where it reduces ambiguity.
- [ ] Add serialization tests.

## Exit criteria

A consumer can identify an exact artifact without using an untyped string convention.

---

# Phase 6 - Agent contract migration

## Goal

Make agents reference exact prompt and skill versions.

## Target AgentDefinition

An agent must be able to reference:

```text
agent identity/version
prompt_ref
skill_refs
tool_refs
policy_refs
provider profile
runtime profile
risk level
lifecycle
state or memory strategy
```

Keep existing fields that still protect a valid runtime boundary.

Do not add a second prompt field after `prompt_ref` becomes authoritative.

## Tasks

- [ ] Replace `capability_refs` with `skill_refs`.
- [ ] Add an exact `prompt_ref`.
- [ ] Remove embedded behavioral instruction fields that duplicate `PromptDefinition`, after migration is complete.
- [ ] Update `AgentConfig`.
- [ ] Update `AgentVersion` and related contracts only where required.
- [ ] Update validation errors with clear skill and prompt terminology.
- [ ] Update public API exports.
- [ ] Add contract tests for exact prompt, skill, tool, and policy references.

## Exit criteria

An agent definition identifies its behavioral instructions and reusable skills by exact version.

---

# Phase 7 - Registry snapshot and dependency graph

## Goal

Make the complete agent artifact graph inspectable and deterministic.

## Required dependency edges

Extend snapshots to support:

```text
agent-to-prompt
agent-to-skill
agent-to-tool
agent-to-policy
skill-to-tool
tool-to-tool
```

Preserve current deterministic ordering and revision evidence.

## Snapshot requirements

A snapshot must make it possible to answer:

- which agent versions exist;
- which prompt version an agent uses;
- which skill versions an agent uses;
- which tools support each skill;
- which exact tools an agent can use;
- which policies constrain the agent;
- which artifact versions were active at snapshot time.

## Tasks

- [ ] Update `RegistryDependency` semantics.
- [ ] Add prompt records to snapshots.
- [ ] Replace capability edges with skill edges.
- [ ] Preserve deterministic ordering.
- [ ] Update snapshot revision calculation where required.
- [ ] Update audit evidence.
- [ ] Add deterministic snapshot tests.

## Exit criteria

The full exact artifact graph is inspectable without importing private implementation code.

---

# Phase 8 - Factory dependency validation

## Goal

Make the agent factory validate the full artifact graph before runtime construction.

## Required checks

For an active agent, validate:

```text
exact PromptDefinition exists and is active
exact SkillDefinition values exist and are active
required skill tools exist and are active
exact agent tool refs exist and are active
exact policy refs exist and are active
risk ceilings remain valid
approval requirements remain valid
provider/runtime profiles remain valid
```

A prompt or skill must never bypass permission, policy, or approval checks.

## Tasks

- [ ] Update `AgentFactory.validate()`.
- [ ] Update dry-run manifest resolution.
- [ ] Update active build behavior.
- [ ] Update dependency error messages.
- [ ] Ensure suspended prompt or skill dependencies stop new governed executions where live-registry rules require this.
- [ ] Add factory tests through public build and execute boundaries.

## Exit criteria

The factory rejects missing, stale, inactive, incompatible, or unauthorized artifact graphs before execution.

---

# Phase 9 - Resolved agent manifest

## Goal

Make build provenance explicit for every exact component.

## Target resolved evidence

A resolved manifest should preserve equivalent information to:

```text
agent_id
agent_version
prompt_id
prompt_version
skills:
  - skill_id
    skill_version
tools:
  - tool_id
    tool_version
policies:
  - policy_id
    policy_version
provider_profile
runtime_profile
registry_snapshot_id
manifest_digest
```

Do not duplicate complete registry records when exact references and a snapshot reference are sufficient.

## Tasks

- [ ] Add prompt provenance.
- [ ] Replace capability provenance with skill provenance.
- [ ] Preserve exact tool and policy provenance.
- [ ] Update manifest digest calculation.
- [ ] Preserve tamper checks.
- [ ] Update `BuiltAgent` validation.
- [ ] Add manifest integrity tests.

## Exit criteria

A built agent has tamper-evident provenance for the exact prompt, skills, tools, policies, and runtime configuration used to construct it.

---

# Phase 10 - Runtime and trace evidence

## Goal

Preserve truthful skill and prompt evidence without inventing model decisions.

## Prompt evidence

Execution evidence should identify the exact prompt version from the resolved manifest.

Do not copy full prompt text into traces by default.

## Skill evidence

At minimum, trace evidence must preserve which skill versions were available to the execution through the resolved manifest or snapshot reference.

Only emit a `skill.selected` event if the runtime has a real explicit skill-selection signal.

Do not infer skill selection only because one of its tools executed.

## Tasks

- [ ] Add prompt version provenance to trace metadata where appropriate.
- [ ] Add skill-version availability provenance.
- [ ] Define explicit skill-selection evidence only if supported by the execution path.
- [ ] Keep trace schemas provider-neutral.
- [ ] Keep private reasoning out of traces.
- [ ] Add trace export tests.

## Exit criteria

External systems can distinguish missing skill availability from poor execution behavior without false selection claims.

---

# Phase 11 - Discovery graph queries

## Goal

Make reusable artifact discovery practical without adding domain-specific planning logic.

## Generic discovery operations

Add only queries that are broadly useful, such as:

```text
skills_for_agent(agent_id, version)
tools_for_skill(skill_id, version)
agents_using_skill(skill_id, version)
agents_using_tool(tool_id, version)
agents_using_prompt(prompt_id, version)
```

These queries return discovery information only.

They do not grant execution authority.

They do not select a production deployment.

## Tasks

- [ ] Add focused graph queries where current registry data can answer them directly.
- [ ] Keep exact versions explicit.
- [ ] Return deterministic results.
- [ ] Return copies, not mutable registry state.
- [ ] Add public-boundary tests.

## Exit criteria

A consumer can inspect reuse and dependency relationships without parsing snapshots manually.

---

# Phase 12 - Forward-only migration and cleanup

## Goal

Complete the terminology and contract migration with one architecture.

## Source migration

Update:

- [ ] contracts;
- [ ] registries;
- [ ] factory;
- [ ] runtime integration;
- [ ] composition and delegation where they reference capabilities;
- [ ] traces and audit records;
- [ ] state or manifest serialization where relevant;
- [ ] package exports.

## Documentation migration

Update:

- [ ] `README.md`;
- [ ] `docs/architecture.md`;
- [ ] `docs/public-api.md`;
- [ ] `docs/quickstart.md`;
- [ ] `docs/product-brief.md`;
- [ ] `docs/production-extension-points.md`;
- [ ] architecture diagrams;
- [ ] relevant ADRs;
- [ ] `docs/BUILD_PLAN.md`.

Do not rewrite historical ADR decisions silently.

Add a new ADR that supersedes capability terminology where required.

## Examples and tests

Update:

- [ ] factory examples;
- [ ] composition examples;
- [ ] approval examples if agent construction changes;
- [ ] registry tests;
- [ ] factory tests;
- [ ] runtime tests;
- [ ] trace tests;
- [ ] public API tests.

## Remove migration residue

Remove:

```text
CapabilityDefinition
CapabilityRegistry
capability_refs
capability-specific dependency names
compatibility aliases
unused migration helpers
```

Do not leave a permanent dual architecture.

## Exit criteria

The repository uses one consistent model:

```text
Agent = governed deployable actor
Skill = reusable ability
Prompt = versioned behavioral instructions
Tool = atomic executable operation
Policy = deterministic authority constraint
```

---

# Public API target

The final public API should expose stable concepts equivalent to:

```text
AgentDefinition
AgentConfig
AgentVersion
AgentRegistry
AgentFactory
BuiltAgent
ResolvedAgentManifest

SkillDefinition
SkillRegistry

PromptDefinition
PromptRegistry

ToolDefinition
ToolRegistry

PolicyDefinition

ComponentType
ComponentReference

RegistryDependency
RegistrySnapshot
```

Keep the exact public surface as small as possible.

Do not export internal implementation helpers.

---

# Non-goals

Do not add:

- gap diagnosis;
- opportunity discovery;
- improvement recommendations;
- evaluation datasets;
- candidate scoring;
- promotion decisions;
- autonomous production deployment;
- arbitrary code generation;
- prompt optimization loops;
- domain-specific skill taxonomies;
- domain-specific tool catalogs;
- business-specific agent planners.

These responsibilities belong outside the core runtime.

---

# Architecture invariants

The implementation must preserve these rules.

1. Model output is untrusted proposal data.
2. Prompts cannot grant authority.
3. Skills cannot grant authority.
4. Tools execute only after validation and governance checks.
5. Policy remains deterministic and application-owned.
6. Approval remains an exact action boundary.
7. Delegation cannot increase authority.
8. Exact registry versions remain immutable.
9. Active dependency checks remain effective for new execution and resume.
10. Registry search remains discovery, not authority.
11. Resolved manifests remain tamper-evident.
12. Trace evidence must not claim facts that the runtime did not observe.
13. The core package remains provider-neutral.
14. The core package remains usable without a model API key.

---

# Recommended implementation passes

## Pass 1 - Semantic foundation

Use high-reasoning implementation work.

Implement:

- Phase 0;
- Phase 1;
- Phase 2;
- Phase 3;
- Phase 4;
- Phase 5.

Exit with stable artifact contracts and registries.

## Pass 2 - Factory and evidence integration

Implement:

- Phase 6;
- Phase 7;
- Phase 8;
- Phase 9;
- Phase 10;
- Phase 11.

Exit with full dependency validation and exact provenance.

## Pass 3 - Migration and release cleanup

Implement Phase 12.

Run the full repository quality gate.

Remove all obsolete capability terminology before completion.

---

# Final acceptance criteria

The work is complete when all of these statements are true:

- `SkillDefinition` is the only reusable ability contract.
- `PromptDefinition` is a first-class exact-version artifact.
- `AgentDefinition` references exact prompt and skill versions.
- `ToolDefinition` remains a separate atomic execution contract.
- `SkillRegistry`, `PromptRegistry`, `ToolRegistry`, and `AgentRegistry` have clear separate responsibilities.
- Registry snapshots include exact prompt, skill, tool, policy, and agent relationships.
- The factory validates the complete artifact graph before build.
- The resolved manifest records exact prompt and skill provenance.
- Runtime evidence does not invent skill-selection claims.
- Prompt and skill metadata cannot increase execution authority.
- Public discovery supports reuse and dependency inspection.
- Capability terminology is removed from active contracts, code, tests, examples, and current documentation.
- Existing governance, approval, delegation, state, observability, and provider-neutral behavior remain intact.
- Formatting, linting, type checks, and the full test suite pass.
