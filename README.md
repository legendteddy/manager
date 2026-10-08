# Manager

**A governed adaptive agent orchestration system.**

Manager is a provider-neutral framework for deciding how AI work should be executed, delegated, evaluated, approved, and reconciled. Its default is deliberately small: keep simple work direct and add agentic complexity only when it materially improves the result or control of the work.

Manager is a clean public successor to an earlier private agent-governance architecture. The private reference remains migration evidence only. This repository does not expose its identity, mappings, configuration, or operational state.

## Status

**Release-readiness audit stage.** Manager now has provider-neutral machine-readable contracts, deterministic behavioral eval fixtures, a Python reference control plane, a provider-neutral model adapter boundary, an OpenAI Responses API reference adapter, governed custom-tool execution, durable approval checkpoints, bounded multi-step model/tool continuation, resumable loop checkpoints, full contract-conformance testing, legal durable-state transition validation, explicit evidence-based recovery resolution, a Manager-owned MCP adapter boundary, end-to-end stdio plus Streamable HTTP protocol tests through the official MCP Python SDK, an explicit threat model, a pre-stable API/versioning policy, and package build/install smoke testing.

The current classification is **experimental reference implementation**. The project has not published a tagged public software release. It does not claim production readiness, behavioral parity with the private reference, arbitrary production tool access, automatic trust of MCP servers, provider-managed MCP execution, production OAuth/credential handling, TLS/mTLS or enterprise proxy policy, request-scoped SSE response-stream conformance, exactly-once external side effects, distributed state/locking, provider failover, transaction rollback orchestration, or unrestricted autonomous agent execution.

See [`docs/release-readiness.md`](docs/release-readiness.md) for the release gates and remaining gaps, and [`docs/threat-model.md`](docs/threat-model.md) for current security assumptions and residual risks.

## Core principles

- **Minimum necessary agentic complexity.** Simple tasks stay direct.
- **Capabilities before agents.** A specialization may be performed by the primary model, deterministic code, a tool, an external service, or a specialist agent.
- **Bounded delegation.** Delegation transfers scoped work, not unlimited authority.
- **Explicit accountability.** Every substantive decision or sub-decision has one accountable owner.
- **Consequential approval.** Material, destructive, sensitive, or otherwise consequential actions cross a human approval gate.
- **Resumable approval.** Approval survives interruption only as a durable, exact checkpoint that must be revalidated before execution.
- **Bounded iteration.** Multi-step model/tool execution uses finite budgets, loop detection, and fresh policy checks at every step.
- **Durable loop identity.** Restart preserves consumed budgets, seen-action fingerprints, provider/model identity, and trusted tool-definition fingerprints instead of granting a fresh execution context.
- **Fail-closed persistence.** Corrupted state, impossible transitions, unsupported checkpoint versions, and uncertain external outcomes stop execution rather than being guessed through.
- **Contract conformance.** Representative emitted runtime artifacts must validate against the public machine-readable contracts in CI.
- **Untrusted interoperability.** External protocols may expose capabilities, but Manager retains local authority over classification, authorization, approval, verification, and model exposure.
- **Authoritative reconciliation.** Update the source that owns the truth first, then propagate dependent effects and verify consistency.
- **Evidence over ceremony.** Evals and observable outcomes matter more than agent count, role-play, or verbose traces.
- **Provider neutrality.** Canonical contracts do not depend on one model vendor, tool protocol, state store, or product surface.
- **Public by default, private by exclusion.** Public framework artifacts live here; private configuration and real operational knowledge stay external.

## Conceptual lifecycle

```text
request
  ↓
frame consequence + uncertainty
  ↓
route the smallest sufficient workflow
  ↓
execute directly / use tools / delegate when justified
  ↓
apply policy, approval, and evaluation gates
  ↓
continue only within explicit model/tool budgets
  ↓
checkpoint normalized model/tool state between durable phases
  ↓
checkpoint exact approval before consequential side effects
  ↓
revalidate before resumed execution
  ↓
continue from preserved loop budgets + seen actions
  ↓
if outcome is uncertain: recovery_required
  ↓
resolve only from explicit external evidence
  ↓
reconcile authoritative state
  ↓
verify
  ↓
deliver
```

These are logical checkpoints, not mandatory agent handoffs. A simple task may collapse them into one execution.

## Logical components

```text
Human principal
      ↓
orchestrator
      ↓
task classification + routing + policy
      ↓
capabilities / specialist agents / tools / interoperability adapters
      ↓
evaluation + approval when required
      ↓
bounded model/tool continuation when justified
      ↓
durable loop/run state for interrupted work
      ↓
execution
      ↓
recovery + reconciliation + verification
      ↓
tracing + evals + schema conformance
```

Internal components use conventional technical terms such as `orchestrator`, `router`, `capability`, `specialist agent`, `policy`, `approval`, `state`, `evaluator`, `reconciliation`, `tracing`, `eval`, and `adapter`.

## Provider, tool, MCP, loop, and state boundary

Manager's deterministic governance remains outside model providers, MCP servers, and tool implementations.

```text
Manager control plane
      ↓
provider-neutral model request
      ↓
model adapter
      ↓
model response / tool proposal
      ↓
trusted ToolRegistry + deterministic policy
      ↓
native tool adapter OR Manager-owned MCP adapter
      ↓
analysis/read → execute + checkpoint sanitized result
consequential → durable approval checkpoint first
      ↓
verification
      ↓
sanitized verified result
      ↓
bounded model continuation when budget remains
      ↓
persisted loop checkpoint for restart/resume
```

A model response supplies content or proposes a tool inside an already-bounded workflow. It cannot approve a material action, lower a tool's side-effect class, grant itself authorization, redefine state ownership, resolve an uncertain external side effect, or bypass reconciliation.

An MCP server may advertise tool names, schemas, descriptions, and annotations. Manager treats those values as untrusted discovery metadata. Only explicitly configured bindings enter the trusted registry, and local Manager configuration remains authoritative for descriptions, side-effect class, verification, sensitivity, and versioning. Schema or binding drift fails closed at registration and is revalidated again immediately before execution.

The first reference provider adapter targets OpenAI's Responses API. The adapter normalizes custom function calls into proposals and maps verified continuation results through `previous_response_id` plus `function_call_output`; execution remains application-owned.

The optional MCP reference bridge targets the official MCP Python SDK v2 line. Connection targets, process commands, URLs, credentials, OAuth configuration, and other environment-specific settings stay outside Manager's canonical contracts. CI exercises the bridge end to end against both a synthetic local stdio MCP subprocess and a synthetic local Streamable HTTP server. The HTTP suite covers governed calls, schema drift, server restart, timeout/cancellation, same-origin and cross-origin redirect behavior, externally supplied synthetic headers, and malformed-response rejection.

The first durable state adapter uses SQLite through Python's standard library. It is a reference durability layer, not an encryption boundary, distributed lock service, or universal production datastore recommendation.

## Reference runtime

The first reference runtime is implemented in Python under [`runtime/python/`](runtime/python/). Python is an implementation choice for the reference runtime, not a canonical requirement for Manager.

From the repository root:

```bash
PYTHONPATH=runtime/python python3 -m manager_runtime.evals evals/cases
PYTHONPATH=runtime/python python3 -m unittest discover -s runtime/python/tests -v
python3 -m pip install 'jsonschema>=4.23,<5'
PYTHONPATH=runtime/python python3 scripts/schema_conformance.py
```

Optional reference integrations:

```bash
python3 -m pip install -e 'runtime/python[openai]'
python3 -m pip install -e 'runtime/python[mcp]'
PYTHONPATH=runtime/python python3 -m unittest discover -s runtime/python/tests -p 'test_mcp_transport_conformance.py' -v
PYTHONPATH=runtime/python python3 -m unittest discover -s runtime/python/tests -p 'test_mcp_streamable_http_conformance.py' -v
```

The deterministic eval and base unit-test suite does not require provider credentials, live external MCP services, live production tools, network model calls, or production state stores. Dedicated MCP transport suites use only synthetic local stdio and loopback HTTP servers. Schema conformance uses `jsonschema` as a test-only dependency. CI also builds the Python wheel, installs it into an isolated target directory, and verifies that the package imports.

## Manager, MCP, and product integrations

Manager is the core framework/runtime concept.

- **Agent harness**: runtime environment around execution.
- **MCP**: optional interoperability adapter, not Manager's identity or authority source.
- **Product/plugin integration**: optional distribution or integration surface.

Manager-owned MCP adapters keep discovery and execution behind the same trusted `ToolRegistry`, policy, approval, verification, redaction, and durable-state boundaries as native tools. Provider-managed MCP execution is not enabled in the reference path because tool execution must remain behind Manager's policy and approval boundary unless equivalent enforcement is proven.

## Repository map

- [`ARCHITECTURE.md`](ARCHITECTURE.md): logical architecture and invariants
- [`GOVERNANCE.md`](GOVERNANCE.md): authority, accountability, materiality, approvals, and bounded evolution
- [`SECURITY.md`](SECURITY.md): untrusted content, access, side effects, secrets, persistence, recovery, interoperability, and public safety
- [`CHANGELOG.md`](CHANGELOG.md): unreleased user-visible changes and future release history
- [`AGENTS.md`](AGENTS.md): instructions for AI systems working in this repository
- [`contracts/`](contracts/): provider-neutral machine-readable contracts
- [`runtime/python/`](runtime/python/): Python reference control plane, provider adapters, governed tool runtime, MCP adapter boundary, durable run store, bounded agent loop, durable loop-resume state machine, and recovery controls
- [`docs/release-readiness.md`](docs/release-readiness.md): release classifications, versioning policy, audit matrix, and first-release gates
- [`docs/threat-model.md`](docs/threat-model.md): trust boundaries, threats, mitigations, residual risks, and security-review triggers
- [`docs/model-adapters.md`](docs/model-adapters.md): model-provider boundary and data rules
- [`docs/tool-runtime.md`](docs/tool-runtime.md): tool proposal, policy, approval, execution, and verification boundary
- [`docs/mcp-adapters.md`](docs/mcp-adapters.md): MCP discovery, binding, trust, and execution boundary
- [`docs/mcp-transport-conformance.md`](docs/mcp-transport-conformance.md): official SDK stdio and Streamable HTTP transport evidence and limits
- [`docs/agent-loop.md`](docs/agent-loop.md): finite multi-step model/tool continuation and stop conditions
- [`docs/durable-agent-loop.md`](docs/durable-agent-loop.md): persisted loop phases, restart invariants, and durable continuation behavior
- [`docs/run-state.md`](docs/run-state.md): durable checkpoints, resumable approvals, and recovery-required behavior
- [`docs/conformance-recovery.md`](docs/conformance-recovery.md): schema conformance, state transitions, checkpoint versions, and recovery resolution
- [`docs/handoffs.md`](docs/handoffs.md): bounded delegation contract
- [`docs/reconciliation.md`](docs/reconciliation.md): authoritative-state reconciliation
- [`docs/evidence.md`](docs/evidence.md): evidence and claim discipline
- [`docs/protected-surfaces.md`](docs/protected-surfaces.md): governance surfaces excluded from automatic evolution
- [`docs/public-private-boundary.md`](docs/public-private-boundary.md): publication boundary
- [`docs/migration.md`](docs/migration.md): generic migration and parity gates
- [`evals/README.md`](evals/README.md): behavioral eval strategy and execution status
- [`examples/README.md`](examples/README.md): synthetic example policy

## License

Licensed under the [Apache License 2.0](LICENSE).

## Maturity

Manager has an executable deterministic reference control plane, a first model-provider adapter boundary, governed synthetic custom-tool execution, durable exact approval checkpoints, a bounded multi-step agent loop, a tested durable loop-resume path, full Draft 2020-12 conformance checks for representative emitted artifacts, legal state-transition enforcement, corrupted-state rejection, an explicit recovery protocol for uncertain external outcomes, a tested Manager-owned MCP tool-binding boundary, end-to-end official-SDK stdio plus Streamable HTTP transport suites using only synthetic local servers, a documented threat model, modern package metadata, and a wheel build/import gate. Current CI verifies public-repository integrity, Python compilation, unit tests, deterministic behavioral evals, schema conformance, package construction, MCP stdio behavior, and Streamable HTTP behavior without provider credentials or live external side effects.

The repository remains **experimental**. Supported-Python matrix coverage, reproducible dependency baselines, a private vulnerability-reporting path, release automation/provenance, tagged artifacts, independent security assessment, and deployment-specific production evidence remain open release-engineering gaps.

Claims such as "secure", "behaviorally equivalent", "production-ready", "exactly once", "distributed", "trusted MCP", "production HTTP-safe", or "safe for autonomous production side effects" require additional evidence for that exact claim.
