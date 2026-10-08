# Release readiness

Manager separates implementation evidence from release claims. A green test suite is necessary evidence, not a declaration that the project is ready for production use.

## Current classification

**Experimental reference implementation.**

The repository has an executable reference control plane, deterministic policy tests, schema conformance, durable-state tests, synthetic MCP transport conformance, supported-Python matrix testing, and release-candidate construction with a reproducible wheel plus checksummed source distribution. It has not published a tagged software release and does not claim production readiness.

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

The current package supports the stable CPython lines explicitly exercised in CI: 3.11, 3.12, 3.13, and 3.14. Package metadata is bounded to `>=3.11,<3.15`; prerelease or future Python lines are not supported merely because they happen to import successfully.

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

## Stage 14 audit

Status on the Stage 14 baseline:

| Area | Status | Evidence / gap |
| --- | --- | --- |
| Protected default branch | Pass | Active ruleset requires pull requests and the `public-safety` status check; deletion and non-fast-forward updates are blocked. |
| CI permissions | Pass | Required repository workflow requests `contents: read` only. |
| Unit, eval, schema and MCP transport gates | Pass | Required workflow covers deterministic runtime tests, evals, schema conformance, stdio MCP, and Streamable HTTP MCP. |
| Public/private leakage controls | Pass with limits | Repository integrity checks scan tracked content and commit-email metadata for high-confidence patterns; this is not a complete secret scanner. |
| Threat model | Pass | `docs/threat-model.md` records trust boundaries, threats, mitigations, and residual risks. |
| API/compatibility policy | Pass | This document defines the pre-stable Python API and versioning boundary. |
| Package metadata | Pass | The reference package uses SPDX license metadata, includes its license text and README, and exposes project URLs. |
| Supported Python matrix | Pass after Stage 14 | Required CI exercises Python 3.11, 3.12, 3.13, and 3.14, including wheel build/install/import on each line. |
| Dependency compatibility lane | Pass after Stage 14 | The supported-Python matrix continues to exercise the package's declared compatible ranges instead of only one frozen environment. |
| Known-good direct baseline | Pass with limits after Stage 14 | `runtime/python/constraints/known-good.txt` pins direct build, conformance, model-adapter, and MCP dependencies. It is not a complete hashed transitive lock. |
| Wheel reproducibility | Pass after Stage 14 | Required CI builds the wheel twice from the same commit under the same verified build inputs and requires matching SHA-256 hashes. |
| Source distribution identity | Pass with explicit limit after Stage 14 | The sdist is built and checksummed, but the current Setuptools `.tar.gz` path is not claimed byte-reproducible. |
| Candidate checksums/provenance | Pass after Stage 14 | Candidate construction writes `SHA256SUMS` and an inspectable `provenance.json` bound to commit, version, build epoch, Python, tools, artifact hashes, and verification policy. |
| Candidate artifact workflow | Pass with limits after Stage 14 | Manual read-only workflow can upload a seven-day GitHub Actions candidate artifact; it cannot tag, publish, attest, or mutate repository contents. |
| Changelog discipline | Pass | `CHANGELOG.md` records unreleased changes and distinguishes development history from tagged releases. |
| Private vulnerability reporting | Gap | `SECURITY.md` does not claim that a verified private reporting channel is enabled. |
| Fully locked dependency reconstruction | Gap | The known-good file pins direct dependencies but does not yet provide a complete hashed transitive lock or offline reconstruction guarantee. |
| Cryptographic release provenance | Gap | Candidate provenance is an inspectable JSON record, not a cryptographic attestation. No release signing, SBOM, or package-registry trusted-publishing path is yet approved. |
| Byte-reproducible source distribution | Gap if required | The current sdist is checksum-identified but not byte-reproducible across repeated same-environment builds. This is not currently promoted as a release guarantee. |
| Tagged releases | Gap | No public GitHub release or package release has been published as of this audit baseline. |
| Independent security assessment | Gap | The controls have not been independently security-audited. |
| Production deployment evidence | Gap | No deployment-specific production evidence is claimed. |

## Release-candidate process

See `docs/release-candidate.md` for the candidate build contract, supported Python lines, dependency lanes, wheel reproducibility gate, source-distribution integrity boundary, checksum/provenance outputs, and the separation between candidate construction and publication.

The manual candidate workflow is preparation only. It does not authorize or perform a public release.

## Remaining first-release gates

Before the first public package release:

1. enable and verify an actionable private vulnerability-reporting path;
2. decide whether the first release requires a fully hashed transitive lock or whether the known-good direct baseline is sufficient;
3. decide whether byte-reproducible source distributions are required for the first release or whether checksum identity is sufficient;
4. define the approved cryptographic provenance/signing/SBOM policy for released artifacts;
5. produce a release candidate from the exact intended release commit;
6. verify its checksums, provenance record, changelog, compatibility evidence, and known limitations;
7. bind explicit maintainer approval to the exact commit, version, artifact hashes, and publication target;
8. publish only after that approval.

Do not collapse these gates into a claim that the framework is generally secure or production-ready.
