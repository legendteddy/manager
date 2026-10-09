# Release and production readiness

Manager separates repository evidence from deployment claims. Green tests are necessary evidence, not a guarantee that an arbitrary environment is safe.

## Current classification

**Production-capable reference profile.**

The repository now contains an integrated, deployable single-instance production profile with:

- governed deterministic routing and exact human approval boundaries;
- provider-neutral model adapters with bounded retry/failover semantics and strict normalized-response validation;
- structurally separated untrusted model evidence;
- governed native and MCP tool execution with identity/policy authorization revalidated immediately before side effects;
- durable run state with optimistic revisions, leases, fencing, an operation ledger, crash recovery, no-blind-replay semantics, and canonical persisted JSON validation;
- hostile MCP network/stdio/resource isolation and required transport attack tests;
- bounded telemetry, concurrency admission, queueing, checkpoint size controls, health signals, and operational diagnostics;
- an authenticated bounded HTTP service with strict request parsing, health/readiness probes, graceful drain, and direct/external TLS support;
- non-root containerization, read-only-root compatibility, strict environment configuration, SQLite backup/restore verification, and a vendor-neutral operator runbook;
- supported Python 3.11 through 3.14 testing;
- candidate-specific hash-locked dependency reconstruction, reproducible wheel and normalized sdist builds, deterministic CycloneDX SBOM, provenance/in-toto evidence, and an independent candidate verifier;
- fail-closed OIDC/keyless trusted-publishing machinery that remains disabled until external release authority is configured.

This classification means the repository provides a defensible production reference profile. It does **not** mean every deployment is production-ready. Production readiness remains deployment-specific because identity provider configuration, TLS/DNS, secret management, network topology, datastore topology, backup retention, telemetry retention, SLOs, incident ownership, provider quotas and real-environment load/failure evidence live outside this repository.

The default SQLite profile intentionally remains **single-instance**. Horizontally scaled production deployments require another coordinated `RunStore` implementation that preserves Manager's CAS, lease, fencing, operation-ledger, recovery and migration semantics transactionally. Manager does not claim exactly-once external effects for arbitrary third-party systems.

## Required repository gates

A commit is eligible for the production-capable profile only when the exact revision passes all required repository checks, including:

- repository integrity and protected-surface validation;
- Python 3.11, 3.12, 3.13 and 3.14 compatibility;
- the full unit and deterministic behavioral-eval suites;
- public schema and emitted-artifact conformance;
- clean-source release dependency resolution twice;
- byte-reproducible wheel and normalized-sdist candidate construction in the verified release environment;
- independent candidate verification;
- offline `--require-hashes` dependency reconstruction;
- normal MCP stdio and Streamable HTTP conformance;
- hostile MCP boundary, stdio and HTTP attack suites;
- the deployment/container smoke lane, including non-root identity, read-only-root production configuration, authenticated service startup, readiness, governed API execution and clean termination.

A branch or pull request that has not passed those gates must not inherit the production-capable classification merely because its parent did.

## Release authority

Publishing remains a distinct external commitment. The prepared PyPI path binds authorization to:

- candidate workflow run ID;
- exact commit SHA;
- package version;
- candidate-manifest SHA-256;
- changelog SHA-256;
- successful required CI for the exact commit;
- current `main` head identity;
- intended destination;
- protected `pypi-release` environment approval.

Changing any bound value makes prior release approval stale. The publication workflow is disabled until the corresponding GitHub/PyPI external controls are deliberately configured.

## Supply-chain status

| Area | Status | Boundary |
| --- | --- | --- |
| Python 3.11-3.14 compatibility | IMPLEMENTED | Required matrix. |
| Build backend identity | IMPLEMENTED | Exact build-backend version in `pyproject.toml`. |
| Dependency reconstruction | IMPLEMENTED | Candidate-specific wheelhouse and SHA-256-bound lock. |
| Offline hash verification | REQUIRED CI | `--no-index --require-hashes` reconstruction. |
| Wheel reproducibility | REQUIRED CI | Two clean-source builds must match byte-for-byte. |
| Normalized sdist reproducibility | REQUIRED CI | Same-environment repeated builds must match. |
| SBOM | IMPLEMENTED | Deterministic CycloneDX 1.6 evidence. |
| Provenance / in-toto statement | IMPLEMENTED | Commit, environment, tools, lock, SBOM and artifact subjects are bound. |
| Candidate verification | IMPLEMENTED | Exact file set, checksums, identity and fingerprint verification. |
| Release attack tests | IMPLEMENTED | Tamper, substitution, stale approval, dirty source and reproducibility failures. |
| Keyless attestation | PREPARED | Requires external GitHub release environment/availability. |
| Trusted PyPI publishing | PREPARED, DISABLED | Requires external protected environment and PyPI trusted publisher. |
| Vulnerability visibility | IMPLEMENTED | Scheduled/manual dependency audit. |
| Private vulnerability reporting | EXTERNAL SETUP | Enable and verify in GitHub repository settings. |
| Independent security assessment | EXTERNAL EVIDENCE | Recommended before high-consequence deployment. |

## Deployment-specific production gate

Before a specific deployment is called production-ready, its operator should additionally prove:

1. real identity/credential configuration and revocation behavior;
2. production TLS, DNS, proxy and egress policy;
3. secret rotation and failure behavior;
4. datastore topology, capacity, migration and recovery assumptions;
5. encrypted/off-site backup retention and a restore drill;
6. telemetry backend, alert routing, retention, SLO and error-budget policy;
7. measured request/provider/tool/MCP capacity and overload behavior;
8. incident ownership and rollback procedures;
9. live-environment failure/chaos evidence appropriate to the deployment's consequence level;
10. external-system idempotency/reconciliation controls for consequential side effects.

These are deployment responsibilities, not reasons to weaken Manager's repository safeguards.

## External setup before first PyPI publication

1. enable and verify GitHub private vulnerability reporting;
2. create and protect the `pypi-release` GitHub environment with appropriate independent reviewers;
3. configure the matching PyPI trusted publisher;
4. enable `MANAGER_PYPI_TRUSTED_PUBLISHING_ENABLED` only after those controls are reviewed;
5. build and review a fresh candidate from the exact intended `main` commit;
6. approve the exact candidate fingerprint only after reviewing its changelog, SBOM, provenance, dependency lock and CI evidence.

If `main` moves, the candidate changes, or a bound hash changes, the publication path fails closed and requires a fresh candidate/approval.

## Documentation

See:

- `docs/service-runtime.md` for the production HTTP boundary;
- `docs/deployment.md` and `docs/operator-runbook.md` for deployment and recovery;
- `docs/operations.md` for observability, capacity and SRE guidance;
- `docs/security-identity.md` and `docs/threat-model.md` for identity/security boundaries;
- `docs/release-candidate.md` and `docs/release-security.md` for release evidence and trusted publishing;
- `docs/private-vulnerability-reporting.md` for the required GitHub setting.
