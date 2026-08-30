# Enterprise Agent Harness Instructions

These instructions capture the project decisions and constraints that matter when working in this repository.

## Product

Enterprise Agent Harness is a provider-neutral Python runtime for enterprise AI agents.

It is not a chatbot framework. It is not a domain product. It is not an evaluation platform.

The harness must let applications safely define, instantiate, execute, govern, compose, and observe agents across domains.

Use these product decisions:

- Keep the harness independent of all consuming products.
- Support read tools, write tools, action tools, approval-gated actions, and long-lived workflows.
- Make agent reuse, extension, composition, and capability discovery first-class.
- Prefer declarative agent definitions and reusable runtime components over arbitrary generated code.
- Keep model providers replaceable.
- Keep evaluation and improvement policy outside the core runtime.
- Export structured traces and manifests so external evaluation systems can inspect agent behavior.

## Writing

Always write project documentation in ASD-STE100 Issue 9 Simplified Technical English.

### Key rules

- Use one word for one idea.
- Write short sentences.
- Use active voice.
- Keep one topic in each paragraph.
- Prefer direct technical language.
- Do not add marketing language to architecture or API documentation.
- Define important terms once and use the same term everywhere.

The goal is easy reading and clear transfer of technical information.

## Architecture Rules

- Use modular architecture with clear separation of concerns.
- Code must be easy to explain from public API to runtime, provider, tool, policy, state, and trace output.
- Prefer clear names, small functions, explicit data flow, and straightforward control flow.
- Add abstractions only when they protect a real boundary or remove meaningful repeated complexity.
- Prefer production-ready design over quick hacks.
- Do not keep backward compatibility before a stable public release unless it protects a real consumer.
- Do not add technical debt, temporary stopgaps, hidden coupling, or speculative abstractions.
- Prefer the simplest implementation that fully meets the current requirements.
- Make long-term architectural decisions where the boundary is already clear.
- When there is a meaningful architecture choice with no existing decision, stop and ask before implementation.
- Keep constants and configuration in the correct config or contract layer.
- Do not hide important control flow behind framework magic.
- Keep deterministic policy decisions outside the model.
- Treat model output as untrusted proposal data.
- Application policy is authoritative.
- A provider cannot grant permissions, expand authority, change approval requirements, or modify policy.
- A tool cannot execute before argument validation and permission checks complete.
- High-risk actions must support explicit human approval.
- Delegation must not increase authority beyond the parent execution context.
- Generated or assembled agents must use approved runtime components and registered tools.

## Core Boundaries

The runtime owns:

- agent execution
- bounded plan and act loops
- tool validation
- permission checks
- policy checks
- approval gates
- provider adaptation
- runtime state contracts
- registries
- agent factory behavior
- delegation controls
- structured tracing
- audit events
- execution budgets

The consuming application owns:

- user and service identity
- business policy
- resource authorization data
- business data stores
- external API credentials
- deployment authority
- tenant boundaries
- domain-specific tools
- business-specific approval policy

External evaluation systems own:

- evaluation datasets
- grading policy
- regression criteria
- candidate comparison
- improvement proposals
- promotion decisions that are outside runtime conformance

Do not move these responsibilities across boundaries without an explicit architecture decision.

## Agent Contracts

Agents must be defined through typed, versioned contracts.

A normal agent must declare:

- identity
- version
- goal
- capabilities
- allowed tools
- policies
- provider profile
- runtime limits
- risk level
- approval requirements
- state or memory strategy when used

Do not require new Python code for every standard agent.

The agent factory must assemble approved components from configuration and registry state.

The factory must reject missing, disabled, incompatible, or unauthorized dependencies.

## Tool Rules

Every tool must have:

- stable identity
- version
- typed input schema
- typed output schema
- risk classification
- ownership metadata
- permission requirements
- timeout behavior
- retry policy when retries are safe

Write tools must support idempotency when duplicate execution can cause harm.

Destructive or irreversible tools must require explicit policy and approval controls.

Do not allow arbitrary tool execution from provider output.

## Registry Rules

Maintain separate registries for agents, tools, and capabilities unless a later decision proves one unified model is better.

Registry records must be versioned and auditable.

The registry must support lifecycle states such as active, suspended, deprecated, and retired.

Capability search must let a consumer determine if an existing agent or tool can solve a new problem before creating another agent.

Do not create a new agent when reuse, extension, or composition is sufficient.

## State and Memory

Keep workflow state separate from conversational memory.

Treat memory as optional.

Do not let retrieved or remembered text become policy, identity, or authority.

Durable state implementations must support ownership, version checks, and safe resume.

Do not store more sensitive data than the runtime needs.

## Observability

Every important runtime decision must produce structured evidence.

Trace at least:

- provider calls
- tool proposals
- tool validation
- permission decisions
- policy decisions
- approval transitions
- tool results
- delegation
- state transitions
- retries
- failures
- final outcomes
- token and cost metadata when available

Do not rely on prose logs as the only evidence source.

Keep trace schemas stable enough for external evaluation and replay.

## Security and Safety

Use least privilege by default.

Use deny-by-default permissions.

Separate trusted and untrusted context.

Treat tool output as untrusted data unless the tool contract says otherwise.

Test direct and indirect prompt injection.

Prevent prompt or tool content from changing permissions, policy, identity, or approval requirements.

Add argument integrity checks before sensitive actions.

Redact secrets and sensitive fields from traces where required.

Do not add autonomous production deployment or arbitrary code execution to the core harness.

## Testing

You must prove the operating path before you add tests.

Do not default to unit-style tests for private helpers.

Prefer tests through public boundaries such as:

- agent runtime execution
- provider adapter boundary
- tool registry and tool execution
- permission and policy broker
- approval pause and resume
- state store
- registry lookup
- agent factory
- trace export

Each test must protect at least one of:

- user or operator outcome
- security boundary
- permission boundary
- policy rule
- data integrity rule
- external contract
- replay rule
- cost rule
- idempotency rule

Do not test source-file structure, private attributes, arbitrary implementation constants, or internal wiring.

A refactor must not require test changes when public behavior stays the same.

Use deterministic providers and small deterministic fixtures for most tests.

Use real provider tests only when they protect provider conformance that a fake cannot prove.

Do not over-test. Remove weaker tests when a stronger integration test protects the same behavior.

Every test name must state the behavior it protects.

## Quality

Use Python typing throughout public contracts.

Run formatting, linting, type checks, and tests before commit.

Keep CI fast enough to run on every change.

Do not merge code with known type, lint, or test failures.

Prefer explicit dependency groups and optional provider extras.

Keep the core package usable without a model API key.

## Documentation

Update architecture documents when a boundary changes.

Use ADRs for decisions that are difficult to reverse.

Keep public API examples current.

Document production extension points clearly.

Do not present demo implementations as production-complete.

Keep the build plan in `docs/BUILD_PLAN.md` aligned with implementation status.

## Commit Discipline

Keep commits focused on one coherent change.

Use clear commit messages that state the architectural or behavioral change.

Do not mix broad refactors with unrelated feature work.
