# Release readiness

Manager separates implementation evidence from release claims. A green test suite is necessary evidence, not a declaration that the project is ready for production use.

## Current classification

**Experimental reference implementation.**

The repository has an executable reference control plane, deterministic policy tests, schema conformance, durable-state tests, and synthetic MCP transport conformance. It has not published a tagged software release and does not claim production readiness.

## Maturity labels

### Experimental

Use this label when behavior is implemented and tested but compatibility, operational support, release reproducibility, and deployment assumptions are still evolving.

### Reference-ready

A reference runtime may be described as reference-ready only when all of the following are true for the release candidate:

- required repository checks pass on the exact release commit;
- the distribution package builds from clean source and imports after installation;
- supported Python versions are explicitly tested;
- public API and compatibility policy are documented;
- release notes or changelog entries identify user-visible changes;
- dependency and build-tool baselines are reproducible enough to investigate regressions;
- package metadata and license files are complete;
- security reporting instructions are actionable;
- release artifacts have an identified provenance path;
- no documentation claims exceed the available evidence.

Reference-ready means suitable as a documented reference implementation. It does not mean suitable for a production deployment.

### Production-ready

Production readiness is deployment-specific and cannot be established by this repository alone. It requires evidence for the intended environment, including threat model fit, credentials and identity, network and TLS policy, observability, backup and recovery, operational ownership, dependency governance, incident response, capacity, availability, and external side-effect semantics.

Manager must not use `production-ready` as a repository-wide label without evidence for those conditions.

## Versioning policy

The Python reference runtime currently uses a `0.x.y` version. Until a first stable compatibility commitment is explicitly approved:

- minor `0.x` releases may contain breaking public API changes;
- patch `0.x.y` releases should be backward-compatible bug fixes, test improvements, documentation corrections, or packaging changes where practical;
- breaking contract or checkpoint changes require explicit migration notes and versioned schemas or migration code;
- a version number in `pyproject.toml` is not itself evidence that a public release exists;
- a version bump does not authorize publishing.

The convenience Python surface exported through `manager_runtime.__all__` is the intended package-facing API, but it remains pre-stable until a tagged release defines a compatibility baseline. Internal modules are not compatibility commitments merely because they are importable.

## Release authority

Publishing a package, tag, GitHub Release, container, signed artifact, or other externally distributed release is an external commitment. It requires explicit maintainer intent and verification of the exact release target.

A release candidate should bind at least:

- commit SHA;
- package version;
- changelog/release notes;
- required CI result;
- artifact identity and checksum;
- build environment or reproducible build instructions;
- known limitations and security-reporting path.

If the commit, version, artifact, or material release parameters change, prior release approval is stale.

## Stage 13 audit

Status on the Stage 13 baseline:

| Area | Status | Evidence / gap |
| --- | --- | --- |
| Protected default branch | Pass | Active ruleset requires pull requests and the `public-safety` status check; deletion and non-fast-forward updates are blocked. |
| CI permissions | Pass | Repository workflow requests `contents: read` only. |
| Unit, eval, schema and MCP transport gates | Pass | Required workflow covers deterministic runtime tests, evals, schema conformance, stdio MCP, and Streamable HTTP MCP. |
| Public/private leakage controls | Pass with limits | Repository integrity checks scan tracked content and commit-email metadata for high-confidence patterns; this is not a complete secret scanner. |
| Threat model | Pass after Stage 13 | `docs/threat-model.md` records trust boundaries, threats, mitigations, and residual risks. |
| API/compatibility policy | Pass after Stage 13 | This document defines the pre-stable Python API and versioning boundary. |
| Package metadata | Pass after Stage 13 | The reference package uses current SPDX license metadata, includes its license text and README, and exposes project URLs. |
| Package build/install smoke test | Pass after Stage 13 | CI builds a wheel and imports it from an isolated target directory. |
| Changelog discipline | Pass after Stage 13 | `CHANGELOG.md` records unreleased changes and explicitly distinguishes development history from tagged releases. |
| Supported Python matrix | Gap | `requires-python` is `>=3.11`, but CI currently proves only the runner's system Python. |
| Reproducible dependency baseline | Gap | CI intentionally installs compatible dependency ranges rather than a fully locked, hashed environment. |
| Private vulnerability reporting | Gap | `SECURITY.md` does not claim that a private reporting channel is enabled. |
| Release automation and provenance | Gap | No package publication workflow, release signing policy, SBOM, or artifact attestation path is established. |
| Tagged releases | Gap | No public GitHub release has been published as of this audit baseline. |
| Independent security assessment | Gap | The controls have not been independently security-audited. |
| Production deployment evidence | Gap | No deployment-specific production evidence is claimed. |

## Next release-engineering gates

Before the first public package release, prefer closing these gaps in order:

1. test every claimed supported Python version;
2. establish a known-good dependency/build baseline while retaining a separate compatibility lane for allowed ranges;
3. enable and document a private vulnerability-reporting path;
4. define a release build/publish workflow with least privilege and artifact provenance;
5. produce checksums and release notes from the exact approved commit;
6. run a release-candidate audit against this checklist;
7. publish only after explicit maintainer approval.

Do not collapse these gates into a claim that the framework is generally secure or production-ready.