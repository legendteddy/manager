# Manager

**A governed adaptive agent orchestration system.**

Manager is a provider-neutral framework for deciding how AI work should be executed, delegated, evaluated, approved, and reconciled. Its default is deliberately small: keep simple work direct and add agentic complexity only when it materially improves the result or control of the work.

Manager is a clean public successor to an earlier private agent-governance architecture. The private reference remains migration evidence only. This repository does not expose its identity, mappings, configuration, or operational state.

## Status

**Bounded agent-loop stage.** Manager now has provider-neutral machine-readable contracts, deterministic behavioral eval fixtures, a Python reference control plane, a provider-neutral model adapter boundary, an OpenAI Responses API reference adapter, governed custom-tool execution, durable approval checkpoints, and bounded multi-step model/tool continuation.

The implemented runtime is intentionally narrow. It does not claim production readiness, behavioral parity with the private reference, arbitrary production tool access, exactly-once external side effects, durable whole-loop resumption, distributed state/locking, provider failover, transaction rollback orchestration, or unrestricted autonomous agent execution.

## Core principles

- **Minimum necessary agentic complexity.** Simple tasks stay direct.
- **Capabilities before agents.** A specialization may be performed by the primary model, deterministic code, a tool, an external service, or a specialist agent.
- **Bounded delegation.** Delegation transfers scoped work, not unlimited authority.
- **Explicit accountability.** Every substantive decision or sub-decision has one accountable owner.
- **Consequential approval.** Material, destructive, sensitive, or otherwise consequential actions cross a human approval gate.
- **Resumable approval.** Approval survives interruption only as a durable, exact checkpoint that must be revalidated before execution.
- **Bounded iteration.** Multi-step model/tool execution uses finite budgets, loop detection, and fresh policy checks at every step.
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
checkpoint durable state when interruption is required
  ↓
revalidate before resumed execution
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
capabilities / specialist agents / tools
      ↓
evaluation + approval when required
      ↓
bounded model/tool continuation when justified
      ↓
durable run state for interrupted work
      ↓
execution
      ↓
reconciliation + verification
      ↓
tracing + evals
```

Internal components use conventional technical terms such as `orchestrator`, `router`, `capability`, `specialist agent`, `policy`, `approval`, `state`, `evaluator`, `reconciliation`, `tracing`, `eval`, and `adapter`.

## Provider, tool, loop, and state boundary

Manager's deterministic governance remains outside model providers and tool implementations.

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
execute / block / require approval
      ↓
verification
      ↓
sanitized verified result
      ↓
bounded model continuation when budget remains
      ↓
durable approval checkpoint when interruption is needed
```

A model response supplies content or proposes a tool inside an already-bounded workflow. It cannot approve a material action, lower a tool's side-effect class, grant itself authorization, redefine state ownership, or bypass reconciliation.

The first reference provider adapter targets OpenAI's Responses API. The adapter normalizes custom function calls into proposals and maps verified continuation results through `previous_response_id` plus `function_call_output`; execution remains application-owned.

The first durable state adapter uses SQLite through Python's standard library. It is a reference durability layer, not an encryption boundary or a universal production datastore recommendation.

## Reference runtime

The first reference runtime is implemented in Python under [`runtime/python/`](runtime/python/). Python is an implementation choice for the reference runtime, not a canonical requirement for Manager.

From the repository root:

```bash
PYTHONPATH=runtime/python python3 -m manager_runtime.evals evals/cases
PYTHONPATH=runtime/python python3 -m unittest discover -s runtime/python/tests -v
```

The deterministic eval and unit-test suite does not require provider credentials, live tools, network access, or production state stores.

## Manager, MCP, and product integrations

Manager is the core framework/runtime concept.

- **Agent harness**: runtime environment around execution.
- **MCP**: optional interoperability adapter, not Manager's identity.
- **Product/plugin integration**: optional distribution or integration surface.

Provider- or product-specific behavior belongs behind adapters rather than inside canonical contracts. Provider-managed MCP execution is not enabled in the reference path because tool execution must remain behind Manager's policy and approval boundary.

## Repository map

- [`ARCHITECTURE.md`](ARCHITECTURE.md): logical architecture and invariants
- [`GOVERNANCE.md`](GOVERNANCE.md): authority, accountability, materiality, approvals, and bounded evolution
- [`SECURITY.md`](SECURITY.md): untrusted content, access, side effects, secrets, and public safety
- [`AGENTS.md`](AGENTS.md): instructions for AI systems working in this repository
- [`contracts/`](contracts/): provider-neutral machine-readable contracts
- [`runtime/python/`](runtime/python/): Python reference control plane, provider adapters, governed tool runtime, durable run store, and bounded agent loop
- [`docs/model-adapters.md`](docs/model-adapters.md): model-provider boundary and data rules
- [`docs/tool-runtime.md`](docs/tool-runtime.md): tool proposal, policy, approval, execution, and verification boundary
- [`docs/agent-loop.md`](docs/agent-loop.md): finite multi-step model/tool continuation and stop conditions
- [`docs/run-state.md`](docs/run-state.md): durable checkpoints, resumable approvals, and recovery-required behavior
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

Manager has an executable deterministic reference control plane, a first model-provider adapter boundary, governed synthetic custom-tool execution, a tested durable approval checkpoint/resume path, and a bounded multi-step agent loop with finite budgets and loop detection. Current CI verifies public-repository integrity, Python compilation, unit tests, and deterministic behavioral evals without requiring provider credentials or live external side effects.

Claims such as "secure", "behaviorally equivalent", "production-ready", "exactly once", or "safe for autonomous production side effects" require additional evidence for that exact claim.
