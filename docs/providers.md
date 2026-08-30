# Provider boundary

Status: complete for Phase 2.

Providers propose data. The runtime owns identity, authority, policy,
permission, approval, tool execution, final outcome states, and trace export.
Provider code must not receive tool handlers, credentials, or a mutable
permission object.

## Operations

Every adapter implements the `ProviderAdapter` protocol:

| Operation | Input | Output |
| --- | --- | --- |
| `interpret` | `InterpretationRequest` | `InterpretationResponse` |
| `plan` | `PlanningRequest` | `PlanningResponse` |
| `compose` | `CompositionRequest` | `CompositionResponse` |

The runtime accepts canonical response models and normalizes compatible
provider mappings at the boundary. Tool calls become `PlanStep` values with a
tool ID, optional version, purpose, and JSON-object arguments. The runtime
then resolves the exact registered tool and repeats validation and governance
checks. A provider cannot add authority by returning a different tool list or
claiming that an action is approved.

## Test provider

Use `DeterministicProvider` for offline tests and conformance probes. It emits
typed responses and does not make network calls:

```python
runtime = AgentRuntime(
    tools=tool_registry,
    provider=DeterministicProvider(),
)
```

`run_conformance_probe` checks interpretation, planning, and composition at
the public provider boundary. Provider tests should inject a fake client or
use this deterministic provider.

## OpenAI adapter

Install the optional dependency only when an integration selects it:

```text
python -m pip install -e ".[openai]"
```

Then construct `OpenAIProviderAdapter(model="...")`. The adapter uses the
OpenAI Responses API for function tools and structured JSON output. See the
[Responses API reference](https://developers.openai.com/api/reference/cli/resources/responses/methods/create)
for the provider-side request and response fields.

The adapter accepts an injected client with a `responses.create` method. This
keeps tests deterministic and keeps the core package independent of the SDK
and API credentials.

## Timeout, retry, and trace metadata

`RuntimeConfig` supplies finite provider timeout, maximum attempts, retry
backoff, and the run-level retry budget. Pass a `ProviderCallPolicy` when the
application needs operation-specific behavior. The default policy allows one
attempt and reports retryable provider timeouts and transient connection
failures. `AgentRuntime` still applies its shared `max_retries` budget to any
provider policy.

Each successful provider call adds a `ProviderCallRecord` to
`RunTrace.provider_calls`. The record contains provider ID, provider version,
model, request ID, latency, retry count, and available token counts. Raw
provider prompts and response content stay out of the trace by default.
