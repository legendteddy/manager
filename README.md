# Manager

**A governed adaptive agent orchestration system.**

Manager is a provider-neutral framework for deciding how AI work should be executed, delegated, evaluated, approved, and reconciled. Its default is deliberately small: keep simple work direct and add agentic complexity only when it materially improves the result or control of the work.

Manager is a clean public successor to an earlier private agent-governance architecture. The private reference remains migration evidence only. This repository does not expose its identity, mappings, configuration, or operational state.

## Status

**Early reference-runtime stage.** The repository defines architecture, governance, security, machine-readable contracts, synthetic eval fixtures, and a small Python reference control plane. The reference runtime demonstrates selected deterministic routing, approval, handoff, reconciliation, and public-safety behaviors. It does not yet claim a production runtime, general-purpose task execution, behavioral parity with the private reference, or production readiness.

## Core principles

- **Minimum necessary agentic complexity.** Simple tasks stay direct.
- **Capabilities before agents.** A specialization may be performed by the primary agent, deterministic code, a tool, an external service, or a specialist agent.
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

## Reference runtime

The first reference runtime is implemented in Python under [`runtime/python/`](runtime/python/). Python is an implementation choice for the reference runtime, not a canonical requirement for Manager.

From the repository root:

```bash
PYTHONPATH=runtime/python python3 -m manager_runtime.evals evals/cases
```

The current runtime is a deterministic control plane. It does not call model providers or execute arbitrary external side effects.

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
- [`runtime/python/`](runtime/python/): first reference control-plane runtime
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

Manager has a language-neutral architectural foundation, public machine-readable contracts, synthetic deterministic eval fixtures, and a small Python reference control plane. It does not yet have provider adapters, general-purpose orchestration, durable approval/state infrastructure, or production side effects. Claims such as "validated", "secure", "behaviorally equivalent", or "production-ready" require evidence for that exact claim.
