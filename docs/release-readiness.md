# Release readiness

Manager separates implementation evidence from release claims. A green test suite is necessary evidence, not a declaration that the project is production-ready.

## Current classification

**Experimental reference implementation.**

The repository has an executable governed reference runtime, supported-Python compatibility testing, deterministic release-candidate construction, same-environment wheel and normalized-sdist reproducibility checks, candidate-specific hash-locked dependency reconstruction, deterministic SBOM/provenance output, an independent candidate verifier, and a fail-closed trusted-publishing design. No tagged or package release is implied by those controls.

## Maturity labels

### Experimental

Behavior is implemented and tested, but compatibility, operations, deployment assumptions, and public release commitments may still evolve.

### Reference-ready

A candidate may be considered reference-ready only when the exact release commit satisfies all of the following:

- required repository checks pass;
- the package builds from clean source and imports after installation;
- Python 3.11, 3.12, 3.13, and 3.14 compatibility checks pass;
- release notes/changelog are reviewed;
- exact candidate artifacts and dependency wheelhouse are checksum-bound;
- offline dependency reconstruction succeeds with `--require-hashes`;
- wheel and normalized sdist reproducibility gates pass in the verified release environment;
- the SBOM and provenance identities verify;
- the independent candidate verifier passes;
- security reporting instructions are actionable;
- the maintainer has explicitly decided the release destination and approved the exact candidate fingerprint.

Reference-ready does not mean production-ready.

### Production-ready

Production readiness is deployment-specific and cannot be established by this repository alone. It requires environment-specific evidence for identity, credentials, network/TLS policy, storage, backup and recovery, observability, capacity, availability, incident response, dependency governance, side-effect semantics, and operational ownership.

## Versioning policy

The Python runtime remains `0.x.y` until a first stable compatibility commitment is explicitly approved. Minor `0.x` releases may contain breaking API changes. Patch releases should be backward-compatible where practical. Version bumps do not authorize publication.

The package supports `>=3.11,<3.15` and CI explicitly covers Python 3.11 through 3.14.

## Release authority

Publishing is an external commitment. Approval must bind to the exact action reviewed.

For the prepared PyPI path, the authorization packet binds:

- candidate workflow run ID;
- commit SHA;
- package version;
- candidate-manifest SHA-256;
- changelog SHA-256;
- required `public-safety` CI result on the exact commit;
- current `main` head identity;
- intended destination;
- protected `pypi-release` environment approval.

Changing any bound value makes the prior approval stale.

## Supply-chain readiness status

| Area | Status | Evidence / remaining boundary |
| --- | --- | --- |
| Supported Python compatibility | IMPLEMENTED | Required matrix covers 3.11-3.14 independently of the release lock. |
| Build backend identity | IMPLEMENTED | `pyproject.toml` exact-pins the Setuptools build backend used by the release path. |
| Release dependency reconstruction | IMPLEMENTED | Exact roots are resolved into a candidate-specific wheelhouse; every wheel is SHA-256 bound in a generated lock. |
| Offline hash verification | VERIFIED IN CI DESIGN | Required CI reconstructs from the bundled wheelhouse with `--no-index --require-hashes`. |
| Wheel reproducibility | VERIFIED IN CI DESIGN | Two same-run candidate builds must have identical wheel bytes. |
| Source distribution reproducibility | IMPLEMENTED | Sdist archive metadata is normalized and repeated same-environment builds must match byte-for-byte. |
| SBOM | IMPLEMENTED | Deterministic CycloneDX 1.6 JSON is bound to commit, artifact hashes, and dependency-lock identity. |
| Provenance | IMPLEMENTED | Provenance v2 plus an in-toto/SLSA-shaped local statement bind commit, environment, tools, artifacts, dependency lock, and SBOM. |
| Independent candidate verification | IMPLEMENTED | `scripts/verify_release_candidate.py` rejects file-set drift, checksum drift, identity drift, lock drift, and stale fingerprints. |
| Release attack tests | IMPLEMENTED | Tamper, substitution, stale approval, dirty-tree, and reproducibility failure cases are covered. |
| Keyless cryptographic attestation | PREPARED | Protected publish workflow uses GitHub keyless attestations only after release approval. External GitHub availability/configuration still applies. |
| Trusted PyPI publishing | PREPARED, DISABLED | Workflow uses OIDC and no long-lived registry token, but a repository variable, protected environment, and PyPI trusted-publisher configuration must be explicitly set. |
| Vulnerability visibility | IMPLEMENTED | Scheduled/manual `pip-audit` is separate from deterministic offline unit tests. |
| Private vulnerability reporting | REQUIRES EXTERNAL SETUP | Repository settings must enable GitHub private vulnerability reporting; see `docs/private-vulnerability-reporting.md`. |
| Tagged/package release | REQUIRES MAINTAINER DECISION | No tag, GitHub Release, or package upload is authorized by these changes. |
| Independent security assessment | REQUIRES EXTERNAL SETUP | Repository controls have not been independently security-audited. |
| Deployment production evidence | REQUIRES EXTERNAL SETUP | Depends on each intended environment. |

`VERIFIED IN CI DESIGN` means the exact branch CI must still execute successfully before the claim is promoted to evidence for a specific commit. The repository must not invent a pass result before that run exists.

## External setup before first PyPI publication

The repository-resolvable machinery is intentionally fail-closed until maintainers complete these external steps:

1. enable GitHub private vulnerability reporting and verify the private report flow;
2. create GitHub environment `pypi-release` and require appropriate maintainer reviewers;
3. configure the PyPI trusted publisher for `legendteddy/manager`, workflow `release-publish.yml`, environment `pypi-release`;
4. only after those controls are reviewed, set repository variable `MANAGER_PYPI_TRUSTED_PUBLISHING_ENABLED=true`;
5. build a fresh candidate from the exact intended `main` commit;
6. review its candidate-manifest SHA-256, changelog hash, SBOM, provenance, lock, CI result, and known limitations;
7. dispatch the publish workflow with those exact values;
8. approve the protected environment only if the displayed authorization packet matches the reviewed candidate.

If `main` moves, the candidate changes, or any supplied hash changes, the workflow fails closed and a fresh candidate/approval is required.

## Documentation

See:

- `docs/release-candidate.md` for the candidate and verification contract;
- `docs/release-security.md` for signing, attestation, trusted publishing, and residual limits;
- `docs/private-vulnerability-reporting.md` for the required GitHub setting.
