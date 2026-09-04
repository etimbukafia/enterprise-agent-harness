# Development workflow

Status: accepted baseline through Phase 15.

## Supported layout

The project uses a `src/` layout. The package is under
`src/enterprise_agent_harness`. Tests are under `tests/` and call public
boundaries. They do not import private helpers to prove internal structure.

```text
.
├── docs/                         # Product, architecture, ADRs, and build plan
├── src/enterprise_agent_harness/ # Installable runtime package
├── tests/                        # Public-boundary and contract tests
├── pyproject.toml                # Build and tool configuration
└── .github/workflows/ci.yml     # Required quality workflow
```

The package directories have one responsibility each:

| Directory | Boundary |
| --- | --- |
| `contracts.py` | Shared typed contracts. |
| `errors.py` | Stable runtime and provider error taxonomy. |
| `providers/` | Provider adapters and conformance probes. |
| `tools/` | Typed tool definitions and resolution. |
| `governance/` | Permission and safety decisions. |
| `runtime/` | Context compilation and execution coordination. |
| `state/` | Principal-bound in-memory and SQLite workflow-state storage. |
| `memory/` | Optional memory strategies. |
| `observability/` | Audit, trace, metrics, and redaction boundaries. |
| `verification/` | Provider-output verification. |
| `evaluation/` | Stable trace, manifest, test-case, and recorded-replay contracts. |
| `prompts/` | Prompt contracts and registry export. |
| `skills/` | Skill contracts and registry export. |
| `registries.py` | Versioned agent, prompt, and skill discovery, lifecycle, compatibility, and snapshots. |
| `factory.py` | Declarative exact-component resolution, runtime profiles, templates, manifests, and agent assembly. |
| `composition.py` | Runtime-only delegation, parent-child ceilings, composition patterns, depth, cycles, and correlation. |
| `background/` | Event envelope, trigger, lease, deduplication, and background job runner contracts with deterministic stores. |

## Local commands

Create an environment and install the development extra:

```text
python -m pip install -e ".[dev]"
```

Run the same checks as CI:

```text
python -m ruff format --check src tests examples
python -m ruff check src tests examples
python -m mypy src
python -m pytest -q
python -m compileall -q src tests examples
```

The core package must run without a model API key. Use the deterministic
provider for normal tests. Add a real provider test only when a fake provider
cannot prove the provider contract.

Run the reference examples from the repository root. They use the source
layout without an installed package:

```text
$env:PYTHONPATH = "src"
python -m examples.quickstart
```

Install the optional OpenAI adapter only for an integration that selects it:

```text
python -m pip install -e ".[openai]"
```

Inject a fake `responses.create` client in tests. Do not make network calls or
require provider credentials in the core test suite.

## Quality rules

- Format with Ruff.
- Lint with Ruff.
- Type-check public code with mypy in strict mode.
- Run tests through public runtime, provider, tool, governance, state, registry,
  factory, composition, and trace boundaries.
- Compile the package before a release check.
- Keep CI commands reproducible from `pyproject.toml` and this document.

The quality workflow runs on pushes and pull requests. It tests the supported
Python versions and runs formatting, linting, typing, tests, and compilation.
It does not publish a package or deploy an application.

## Change workflow

1. Update the relevant contract or architecture document first when a
   boundary changes.
2. Add a public-boundary test for the user, security, policy, data-integrity,
   replay, cost, or idempotency rule that the change protects.
3. Run all local commands above.
4. Update the build plan and the relevant ADR when the decision is difficult
   to reverse.
