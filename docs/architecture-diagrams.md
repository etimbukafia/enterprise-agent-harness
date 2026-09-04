# Architecture diagrams

These diagrams show the main public paths. A box that says `application` is
owned by the consuming application.

## Governed execution

```mermaid
flowchart LR
    A[Application identity and input] --> B[BuiltAgent.execute]
    B --> C[AgentRuntime]
    C --> D[Provider proposal]
    D --> E[Plan and argument validation]
    E --> F[Permission and policy]
    F --> G[Registered tool handler]
    G --> H[Runtime outcome and trace]
```

The provider proposes work. The runtime decides if the tool can run.

## Versioned artifact graph

```mermaid
flowchart TD
    A[AgentDefinition] -->|prompt_ref| P[PromptDefinition]
    A -->|skill_refs| S[SkillDefinition]
    A -->|tool_refs| T[ToolDefinition]
    A -->|policy_refs| Y[PolicyDefinition]
    S -->|required or optional tool refs| T
    T -->|exact dependencies| T
    A --> M[ResolvedAgentManifest]
    M -->|registry_snapshot_id and digest| X[RegistrySnapshot]
```

The agent's explicit `tool_refs` are the execution ceiling. Skill links
describe reusable behavior and dependencies; they do not grant a tool.
Snapshots preserve the exact graph used by factory validation.

## Provenance without prompt leakage

```mermaid
flowchart LR
    P[Exact prompt ref] --> R[RunTrace and AuditEvent]
    S[Exact skill refs] --> R
    M[Manifest ID, digest, snapshot ID] --> R
    I[Prompt instructions] --> C[Provider context]
    C -. excluded from trace .-> R
```

Trace and audit evidence identifies the prompt and skills without storing
prompt instructions, raw provider content, or private reasoning by default.

## Approval pause and resume

```mermaid
flowchart LR
    A[Governed action proposal] --> B[ApprovalRequest]
    B --> C[Application reviewer]
    C --> D[ApprovalDecision]
    D --> E[AgentRuntime.resume]
    E --> F[Exact approved action]
    F --> G[Outcome and trace]
```

The request and decision use the same action digest. A missing or stale
decision does not call the handler.

## Router and specialist delegation

```mermaid
flowchart LR
    A[Parent or router] --> B[AgentComposer]
    B --> C[Parent authority ceiling]
    C --> D[Registered specialist]
    D --> E[Child governed runtime]
    E --> F[Child outcome and correlated trace]
```

Delegation can reduce authority. It cannot add tools, permissions, risk, or
delegation depth.

## Event-driven execution

```mermaid
flowchart LR
    A[EventEnvelope] --> B[BackgroundJobRunner]
    B --> C[Deduplication and lease]
    C --> D[AgentRuntime.execute_event]
    D --> E[Normal governed execution]
    E --> F[JobResult and trace]
    B -. same dedup key .-> G[Duplicate result]
```

The runner handles one event identity. A duplicate does not start a second
governed execution after the first event completes.

## External evaluation boundary

```mermaid
flowchart LR
    A[External test case] --> B[TestCaseAdapter]
    B --> C[BuiltAgent execution]
    C --> D[RunTrace and manifest export]
    D --> E[External metric and hard gate]
    D --> F[RecordedReplayAdapter]
    F --> G[Offline evidence]
```

Evaluation code consumes exported evidence. It does not enter the runtime
governance path or perform live actions during recorded replay.
