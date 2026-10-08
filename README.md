# Manager

**A governed adaptive agent orchestration system.**

Manager is a provider-neutral framework for deciding how AI work should be executed, delegated, evaluated, approved, and reconciled. Its default is deliberately small: keep simple work direct and add agentic complexity only when it materially improves the result or control of the work.

Manager is a clean public successor to an earlier private agent-governance architecture. The private reference remains migration evidence only. This repository does not expose its identity, mappings, configuration, or operational state.

## Status

**Model-backed reference-runtime stage.** Manager now has provider-neutral machine-readable contracts, deterministic behavioral eval fixtures, a Python reference control plane, and a provider-neutral model adapter boundary with an OpenAI Responses API reference adapter.

The implemented runtime is intentionally narrow. It does not claim production readiness, behavioral parity with the private reference, arbitrary tool execution, durable approval/state storage, provider failover, or general-purpose autonomous agent execution.

## Core principles

- **Minimum necessary agentic complexity.** Simple tasks stay direct.
- **Capabilities before agents.** A specialization may be performed by the primary model, deterministic code, a tool, an external service, or a specialist agent.
- **Bounded delegation.** Delegation transfers scoped work, not unlimited authority.
- **Explicit accountability.** Every substantive decision or sub-decision has one accountable owner.
- **Consequential approval.** Material, destructive, sensitive, or otherwise consequential actions cross a human approval gate.
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
reconcile authoritative state
  ↓
verify
  ↓
deliver
  ↓
learn from evidence when durable value exists
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
execution
      ↓
reconciliation + verification
      ↓
tracing + evals
```

Internal components use conventional technical terms such as `orchestrator`, `router`, `capability`, `specialist agent`, `policy`, `approval`, `state`, `evaluator`, `reconciliation`, `tracing`, `eval`, and `adapter`.

## Provider boundary

Manager's deterministic governance remains outside model providers.

```text
Manager control plane
      ↓
provider-neutral model request
      ↓
model adapter
      ↓
provider SDK / API
      ↓
normalized model response
      ↓
Manager result + trace
```

A model response supplies content inside an already-authorized workflow. It cannot approve a material action, widen tool permissions, redefine state ownership, or bypass reconciliation.

The first reference provider adapter targets OpenAI's Responses API. It is optional. Canonical Manager contracts remain provider-neutral and do not hard-code a default model.

## Reference runtime

The first reference runtime is implemented in Python under [`runtime/python/`](runtime/python/). Python is an implementation choice for the reference runtime, not a canonical requirement for Manager.

From the repository root:

```bash
PYTHONPATH=runtime/python python3 -m manager_runtime.evals evals/cases
```

The deterministic eval suite does not require provider credentials or network access. Model-backed execution is optional and uses an explicit provider adapter.

## Manager, MCP, and product integrations

Manager is the core framework/runtime concept.

- **Agent harness**: runtime environment around execution.
- **MCP**: optional interoperability adapter, not Manager's identity.
- **Product/plugin integration**: optional distribution or integration surface.

Provider- or product-specific behavior belongs behind adapters rather than inside canonical contracts.

## Repository map

- [`ARCHITECTURE.md`](ARCHITECTURE.md): logical architecture and invariants
- [`GOVERNANCE.md`](GOVERNANCE.md): authority, accountability, materiality, approvals, and bounded evolution
- [`SECURITY.md`](SECURITY.md): untrusted content, access, side effects, secrets, and public safety
- [`AGENTS.md`](AGENTS.md): instructions for AI systems working in this repository
- [`contracts/`](contracts/): provider-neutral machine-readable contracts
- [`runtime/python/`](runtime/python/): Python reference control plane and provider adapters
- [`docs/model-adapters.md`](docs/model-adapters.md): model-provider boundary and data rules
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

Manager has an executable deterministic reference control plane and a first model-provider adapter boundary. Current CI verifies public-repository integrity, Python compilation, unit tests, and deterministic behavioral evals without requiring provider credentials.

Claims such as "secure", "behaviorally equivalent", "production-ready", or "safe for autonomous external side effects" require additional evidence for that exact claim.
