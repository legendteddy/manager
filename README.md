# Manager

**A governed adaptive agent orchestration system.**

Manager is a provider-neutral framework for deciding how AI work should be executed, delegated, evaluated, approved, recovered, and reconciled. Its default remains deliberately small: keep simple work direct and introduce agentic complexity only when it materially improves the result or control of the work.

## Status

**Production-capable reference profile.**

Manager now includes an integrated governed runtime, durable execution/state model, provider resilience layer, identity and authorization boundary, hostile MCP isolation, SRE/capacity controls, authenticated HTTP service, non-root/read-only-root deployment reference, backup/restore tooling, deterministic release construction, hash-locked dependency reconstruction, SBOM/provenance evidence, and required adversarial/transport verification.

This classification means the repository contains a defensible production reference implementation and deployment profile. It does **not** mean any arbitrary deployment is automatically production-ready. A real deployment still has to supply and prove its own identity provider, TLS/DNS, secrets, network/egress policy, datastore topology, backup retention/encryption, telemetry backend, SLOs/on-call ownership, provider quotas, external-side-effect idempotency/reconciliation, and environment-specific load/failure behavior.

The bundled SQLite profile is intentionally **single-instance**. Horizontal production scaling requires a coordinated `RunStore` implementation that preserves Manager's CAS, lease, fencing, durable operation-ledger, recovery, and migration semantics transactionally.

No package publication, tag, GitHub Release, or live production infrastructure is implied by the repository classification.

See [`docs/release-readiness.md`](docs/release-readiness.md) for the exact repository and deployment readiness boundary.

## What Manager provides

### Governed execution

- deterministic routing for routine versus material work;
- exact approval binding for consequential actions;
- approval freshness and authorization revalidation immediately before side effects;
- bounded delegation and finite model/tool budgets;
- repeated-action detection and no approval carry-forward;
- authoritative reconciliation and explicit recovery for uncertain external outcomes.

### Identity and security

- provider-neutral secret-provider boundary;
- signed identity validation and revocation hooks;
- deny-by-default capability authorization over principal, action, resource, environment, side-effect class, and policy revision;
- approval binding to the exact authorization decision;
- strict TLS/proxy/credential-forwarding policy primitives;
- bounded/redacted public error surfaces.

### Durable execution

- optimistic revisions and compare-and-swap;
- leases and fencing tokens;
- durable consequential-operation identities and operation ledger;
- crash/restart recovery without blind replay of uncertain effects;
- canonical JSON persistence and revision-integrity validation;
- fail-closed handling of corrupted or impossible state.

Manager does not claim exactly-once effects for arbitrary third-party systems. External systems still need appropriate idempotency, fencing, verification, or reconciliation semantics.

### Models and providers

- provider-neutral request/response contracts;
- strict normalized provider validation;
- bounded retry/backoff and initial-turn failover semantics;
- provider pinning for continuation;
- structural separation of untrusted evidence from Manager-owned instructions;
- OpenAI Responses reference adapter plus deterministic synthetic provider coverage.

### Tools and MCP

- application-owned `ToolRegistry` remains authoritative for side-effect class, schema, version, verification, and sensitivity;
- bounded JSON-schema/argument validation and immutable registry snapshots;
- same-session schema-bound consequential MCP execution;
- finite timeout, pagination, cursor, item, byte, nesting, stderr, and concurrency limits;
- network/SSRF and stdio process policy;
- required normal and hostile stdio/Streamable HTTP transport tests.

External tool or MCP metadata is evidence, never authority.

### Service and operations

The `manager-service` entrypoint provides a bounded production HTTP boundary:

- `GET /livez`
- `GET /readyz`
- `GET /healthz`
- `POST /v1/run`

Staging and production require bearer authentication backed by a mounted secret. The service uses strict JSON parsing, bounded request bodies, finite worker/queue admission, readiness-aware overload rejection, generic error surfaces, and graceful SIGTERM/SIGINT drain. It does not expose a network path for arbitrary tool registration or approval bypass.

Operational support includes bounded telemetry, low-cardinality metrics, run/model/tool/MCP/state capacity gates, saturation signals, checkpoint-size admission, operator diagnostics, and documented alerting guidance.

### Deployment reference

The repository includes:

- strict development/testing/staging/production configuration;
- non-root UID/GID 10001 container execution;
- read-only-root compatibility;
- external or direct TLS modes;
- single-instance SQLite enforcement;
- online SQLite backup, integrity manifest, verification, and restore-to-new-path safeguards;
- loopback-only external-TLS Compose reference with an external bearer secret;
- CI that boots the real authenticated service, verifies readiness/API behavior, and proves clean termination.

See [`docs/deployment.md`](docs/deployment.md), [`docs/service-runtime.md`](docs/service-runtime.md), and [`docs/operator-runbook.md`](docs/operator-runbook.md).

## Release and supply-chain controls

The release-candidate path includes:

- Python 3.11, 3.12, 3.13, and 3.14 compatibility;
- exact build-backend identity;
- candidate-specific transitive dependency wheelhouse and SHA-256 lock;
- offline `--no-index --require-hashes` reconstruction;
- byte-reproducible wheel and normalized sdist checks in the verified release environment;
- deterministic CycloneDX SBOM;
- provenance plus an in-toto/SLSA-shaped statement;
- exact candidate payload manifest and approval fingerprint;
- independent candidate verifier and tamper/substitution tests;
- pinned CI actions and workflow-input shell-boundary checks;
- prepared OIDC/keyless PyPI publishing that remains disabled until external release authority is configured.

See [`docs/release-candidate.md`](docs/release-candidate.md) and [`docs/release-security.md`](docs/release-security.md).

## Core principles

- **Minimum necessary agentic complexity.** Simple work stays direct.
- **Capabilities before agents.** Use deterministic code, tools, services, models, or specialists according to the job rather than role-play.
- **Bounded delegation.** Delegation transfers scoped work, not unlimited authority.
- **Consequential approval.** Material, destructive, sensitive, or external-commitment actions cross an explicit approval boundary.
- **Fresh authority.** Authentication, authorization, approval, tool identity, and target assumptions are revalidated at execution time.
- **Fail-closed durability.** Corruption, incompatible state, stale workers, unknown outcomes, and unsupported transitions stop rather than guess.
- **Untrusted interoperability.** Providers, retrieved content, tools, and MCP servers cannot redefine Manager's authority.
- **Evidence over ceremony.** Evals, verification, recovery evidence, and observable outcomes matter more than agent count.
- **Provider neutrality.** Canonical contracts do not depend on one model vendor, protocol, datastore, or deployment platform.
- **Public-safe by default.** Private configuration, credentials, infrastructure, and operational knowledge remain outside the public repository.

## Logical execution boundary

```text
request
  ↓
classification + routing
  ↓
identity / policy / capability checks
  ↓
direct work OR bounded model/tool workflow
  ↓
exact approval before consequential action
  ↓
execution-time revalidation
  ↓
durable operation identity + guarded execution
  ↓
verification / recovery_required when uncertain
  ↓
reconciliation
  ↓
trace + result
```

Models may propose work, but they do not grant authority. Tool and MCP servers may expose capabilities, but Manager retains local policy, authorization, approval, and verification control.

## Repository map

- [`contracts/`](contracts/) provider-neutral machine-readable contracts
- [`runtime/python/`](runtime/python/) Python reference and production-capable runtime
- [`evals/`](evals/) deterministic behavioral fixtures
- [`deploy/`](deploy/) container and Compose reference
- [`docs/`](docs/) security, MCP, state, deployment, operations, release, and recovery documentation
- [`.github/workflows/`](.github/workflows/) required integrity, deployment, release, and vulnerability-visibility automation

## Verification boundary

Required repository CI verifies the supported Python matrix, complete unit/eval suite, schema conformance, clean reproducible release candidate construction, offline hash-locked reconstruction, normal MCP transports, hostile MCP boundary/stdio/HTTP attacks, and the authenticated production container path.

Those gates prove the exact repository revision. They do not substitute for environment-specific capacity tests, disaster-recovery drills, penetration testing, or operational ownership in a real deployment.

## License

Apache-2.0. See [`LICENSE`](LICENSE).
