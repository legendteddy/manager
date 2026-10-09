# Release supply-chain security

This document records the release-security architecture without granting release authority.

## Trust model

A release candidate is identified by the SHA-256 of `candidate-manifest.json`. The manifest binds every payload file except itself. The payload in turn binds package artifacts, dependency wheelhouse, dependency lock, hash-locked requirements, SBOM, provenance, and the local in-toto statement.

A reviewer may therefore record one candidate-manifest digest as the exact candidate fingerprint. The independent verifier recomputes that fingerprint and every transitive file hash before publication. It also verifies semantic consistency across the dependency wheelhouse, structured lock, SBOM, Manager provenance and in-toto build predicate; a self-consistently rehashed but contradictory evidence bundle is rejected.

## Dependency model

Manager deliberately does not use one frozen environment for ordinary compatibility testing. The release lane instead starts from exact known-good roots and resolves a complete platform-specific wheel closure for the release Python environment.

The resulting candidate contains the wheel bytes themselves, not only names and versions. Each wheel is hashed. `requirements.lock` is a deterministic rendering of the structured lock and is usable with pip `--require-hashes` and `--no-index`.

The independent verifier requires the `dependencies/` wheelhouse to match the structured dependency lock exactly. An extra, missing, substituted or unhashed dependency payload fails verification even if the candidate manifest has been recomputed.

This makes the candidate reconstructable even if a package index later changes, as long as the candidate bundle itself is retained and verified.

## SBOM

The candidate contains deterministic CycloneDX 1.6 JSON. It records the exact release package identity, source commit, dependency-lock identity, package artifact hashes, and every resolved dependency wheel with version and SHA-256.

The independent verifier compares the SBOM dependency components against the exact dependency lock, including dependency name, version, wheel filename and SHA-256. Omitting or substituting a component is a verification failure rather than merely a manifest-fingerprint change.

No wall-clock generation time is used. The timestamp is derived from the source commit epoch so repeated verified builds can compare SBOM bytes.

## Provenance and attestations

The candidate contains two non-secret provenance records:

- `provenance.json`, an easy-to-inspect Manager record;
- `provenance.intoto.jsonl`, an unsigned in-toto Statement using the SLSA provenance predicate shape.

These records are evidence, not signatures.

The independent verifier checks the in-toto artifact subjects plus the build type, project/version/commit/dependency-lock parameters, resolved source and lock identities, builder identity, invocation identity and SBOM byproduct identity. A stale or contradictory provenance predicate therefore fails even when all candidate files have been rehashed into a new manifest.

The prepared publication workflow uses GitHub's keyless build-provenance attestation action after protected-environment approval. GitHub obtains a short-lived signing identity via OIDC/Sigstore. No long-lived signing private key is stored in the repository.

For released artifacts, consumers should verify both the candidate locally and the GitHub-hosted attestation using GitHub's supported attestation verification tooling.

## Trusted publishing

The prepared PyPI workflow intentionally contains no PyPI API token. It requests `id-token: write` only in the protected publication job and uses PyPI trusted publishing.

Repository configuration must remain fail-closed until maintainers explicitly configure:

- PyPI trusted publisher for this repository;
- workflow `release-publish.yml`;
- protected GitHub environment `pypi-release`;
- required environment reviewers;
- repository variable `MANAGER_PYPI_TRUSTED_PUBLISHING_ENABLED=true`.

The variable is a second enablement gate, not a substitute for environment approval.

## Required CI binding

The publication workflow queries GitHub workflow runs and requires successful push runs for both `repository-integrity.yml` and `deployment-reference.yml` on the exact approved commit. The first workflow covers the supported Python matrix, repository/public-safety checks, deterministic behavioral tests, schema and release-candidate verification, dependency reconstruction and MCP attack lanes. The second supplies the documented deployment/container smoke gate, including non-root/read-only-root production configuration, authenticated service startup, readiness/API exercise and clean termination.

Those exact-commit CI requirements are checked before protected-environment review and checked again immediately before publication. The commit must also still equal current `main` at both boundaries.

This deliberately makes an approval stale if new work lands on `main` while a publication waits for review. Merge-time branch protection may enforce a subset of repository checks; the publication path independently enforces the complete release-specific pair above.

## Workflow integrity

Repository integrity scans every `.yml` and `.yaml` file under `.github/workflows/`, rather than a hand-maintained workflow allowlist. Every external `uses:` reference must be pinned to a 40-hex commit, and workflow-dispatch inputs may not be interpolated directly into shell script bodies.

This prevents a newly added CI or release workflow from silently escaping the same action-pinning and dispatch-input safeguards applied to existing supply-chain workflows.

## Changelog and destination binding

The publish dispatch requires the SHA-256 of `CHANGELOG.md` and explicitly records the destination. The verifier checks the changelog hash against the checked-out release commit.

The current workflow supports only the `pypi` destination. Adding another registry or destination is a material release-policy change and requires maintainer review.

## Vulnerability visibility

A scheduled/manual workflow runs `pip-audit` against the release dependency roots. It is separate from deterministic offline unit tests because vulnerability data is network-dependent and changes over time.

A vulnerability result does not automatically rewrite dependency pins. Remediation still requires compatibility and release evidence.

## Residual limits

The machinery does not prove package indexes, GitHub, PyPI, Sigstore, container registries, mutable reference-image tags, or vulnerability feeds are universally trustworthy or available. It narrows trust by binding exact package/dependency bytes and using short-lived publication identity, but platform compromise and account compromise remain external risks.

The reference Dockerfile and Compose example intentionally remain operator-facing examples. Production/release builds must supply an organization-approved immutable Python base-image digest as documented by those files; the repository does not claim that the mutable convenience default is itself a release identity.

A candidate-specific platform wheelhouse is not a universal cross-platform lock. Releasing artifacts built for a materially different platform or interpreter requires a separately verified lock/reproducibility policy.
