# ADR 0013: First-class prompt and skill artifacts

## Status

Accepted and implemented as the forward-only replacement for the earlier
ability metadata model.

## Context

The runtime needs a small, provider-neutral artifact graph that can be
resolved, audited, composed, and exported without conflating behavioral
instructions, reusable behavior, executable tools, and policy. The earlier
baseline represented reusable behavior under a single legacy term and did not
make prompt provenance or the full cross-component graph explicit.

## Decision

1. `PromptDefinition` is a first-class immutable, versioned artifact with
   `prompt_id`, `version`, `purpose`, `instructions`, owner, lifecycle, and
   metadata. It contains no provider DSL, fragments, inheritance, or hidden
   authority.
2. `SkillDefinition` is a first-class immutable, versioned reusable behavior
   artifact with identity, description, supported operations/intents/languages,
   risk, owner, lifecycle, metadata, and exact required/optional tool
   references. Required tool references must resolve for validation or
   activation. Optional references may be absent and never become an implicit
   grant.
3. `ComponentType` is the closed set `agent`, `prompt`, `skill`, `tool`, and
   `policy`. `ComponentReference` carries the component type, stable ID, and
   exact PEP 440-compatible version. Tool dependencies use the same typed
   reference.
4. `AgentDefinition` and `AgentConfig` contain one exact `prompt_ref`, plus
   exact `skill_refs`, `tool_refs`, and `policy_refs`. The explicit tool list is
   the execution authority ceiling; a skill's tool metadata cannot add to it.
5. Separate `PromptRegistry` and `SkillRegistry` own immutable versions,
   lifecycle transitions, metadata search, exact resolution, audit events, and
   safe-copy discovery. `AgentRegistry` coordinates their active dependency,
   risk, and transitive tool checks.
6. A registry snapshot contains agents, prompts, skills, tools, policies, and
   exact edges for agent-to-prompt, agent-to-skill, agent-to-tool,
   agent-to-policy, skill-to-tool, and tool-to-tool relationships. Ordering is
   deterministic and snapshot identities are stable for the same graph view.
7. A factory manifest stores exact prompt/skill/tool/policy references, provider
   and runtime profile data, the registry snapshot identity, and a digest over
   all authority-affecting fields. It does not duplicate full prompt or skill
   records when the references and snapshot identity provide provenance.
8. Run traces and audit events record exact manifest, registry snapshot, prompt,
   and skill references. They do not store prompt instructions, private model
   reasoning, or raw provider content by default. The runtime emits no
   inferred skill-selection event merely because a linked tool executed.
9. The migration is forward-only. The legacy names, registry, fields, and
   compatibility shims are removed from active source, tests, examples, and
   public exports. Historical ADRs remain unchanged; this ADR supersedes them
   for the active artifact model.

## Consequences

The graph is explicit enough for factory validation, lifecycle enforcement,
discovery, deterministic snapshots, trace correlation, and external audit.
Applications must update configurations to exact prompt, skill, tool, and
policy references and must repeat a skill's required tools in the agent's
explicit tool authority. Provider adapters receive prompt and skill metadata
through typed request contracts and remain unable to change authority.

## Migration matrix

| Legacy concept | Active replacement | Migration rule |
| --- | --- | --- |
| legacy reusable behavior definition | `SkillDefinition` | Create a new exact skill record; map metadata and split required/optional tool references. |
| legacy reusable behavior registry | `SkillRegistry` | Register exact skill versions against the shared `ToolRegistry`. |
| legacy agent behavior references | `AgentDefinition.skill_refs` / `AgentConfig.skill_refs` | Replace each reference with `ComponentReference(component_type="skill", ...)`. |
| legacy executable allowlist | `AgentDefinition.tool_refs` / `AgentConfig.tool_refs` | Use explicit exact tool references; skill metadata never grants authority. |
| embedded behavioral instructions | `PromptDefinition` plus `prompt_ref` | Register one versioned prompt and reference it exactly from the agent. |
| untyped dependency strings | `ComponentReference` | Use typed exact references, including tool-to-tool dependencies. |

