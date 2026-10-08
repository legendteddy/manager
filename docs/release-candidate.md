# Release candidate build

Manager separates release-candidate construction from publication. Candidate construction is preparation and evidence gathering. It does not authorize a tag, GitHub Release, package upload, attestation, or deployment.

## Supported Python and dependency lanes

The Python package supports CPython 3.11 through 3.14 and declares `>=3.11,<3.15`.

Two lanes are intentionally retained:

1. **Compatibility lane.** CI exercises the dependency ranges declared in `pyproject.toml` on every supported Python line. This catches overly narrow or broken compatibility ranges.
2. **Release reconstruction lane.** `runtime/python/constraints/known-good.txt` pins exact release-candidate root versions. On the release Python platform, the candidate workflow resolves the complete wheel closure for those roots, bundles those wheels, hashes every wheel, and emits an exact hash-locked requirements file.

The generated candidate lock is platform-specific by design. It is not advertised as a universal cross-platform lock. This avoids replacing compatibility testing with one frozen environment while still making the exact release candidate independently reconstructable.

The PEP 517 build backend is exact-pinned in `pyproject.toml` and the candidate builder uses `--no-isolation` after verifying the known-good build-tool versions.

## Candidate builder

`scripts/build_release_candidate.py` fails closed on a dirty source tree and then:

1. binds the build to the exact Git commit;
2. derives `SOURCE_DATE_EPOCH` from that commit and fixes `PYTHONHASHSEED`;
3. verifies the exact known-good release roots installed in the build environment;
4. builds one wheel and one source distribution;
5. normalizes source-distribution archive metadata, ownership, gzip timestamp, and member ordering;
6. copies the resolved dependency wheelhouse into the candidate;
7. emits `dependencies.lock.json` and deterministic `requirements.lock` with SHA-256 hashes for every dependency wheel;
8. emits deterministic CycloneDX 1.6 JSON as `sbom.cdx.json`;
9. emits `SHA256SUMS` for the package wheel and sdist;
10. emits strengthened `provenance.json` containing commit, version, clean-tree assertion, build epoch, interpreter/platform identity, build-tool versions, dependency-lock identity, SBOM identity, artifact hashes, and verification policy;
11. emits an unsigned in-toto Statement using the SLSA provenance predicate as `provenance.intoto.jsonl`;
12. emits `candidate-manifest.json`, whose SHA-256 is the approval fingerprint for the complete candidate payload.

The local in-toto statement is structured provenance evidence, not a signature. Keyless cryptographic GitHub attestations are intentionally created only at the separately protected publication boundary.

## Reproducibility claim

Required CI builds two candidates from the same commit, independently resolves the release dependency wheelhouse twice, and requires equality for:

- wheel bytes;
- normalized sdist bytes;
- dependency wheelhouse bytes;
- dependency lock;
- hash-locked requirements;
- SBOM;
- provenance;
- local in-toto statement;
- candidate manifest.

This establishes byte reproducibility in the verified same-run Ubuntu 24.04 / release-Python environment. It is not a cross-platform reproducibility claim.

The normalized sdist remains a standards-compatible `.tar.gz` source distribution. CI also asks pip to build a wheel from the normalized sdist using the already verified build environment.

## Offline reconstruction

After a candidate is built, its release dependency environment can be reconstructed without a package index:

```bash
python -m pip install \
  --no-index \
  --find-links release-candidate/dependencies \
  --require-hashes \
  -r release-candidate/requirements.lock
```

Required CI executes this path. A modified dependency wheel or missing/mismatched hash invalidates the candidate manifest and the independent verifier.

## Independent verification

Use:

```bash
python scripts/verify_release_candidate.py release-candidate \
  --expected-commit <commit-sha> \
  --expected-version <version> \
  --expected-manifest-sha256 <reviewed-manifest-sha256> \
  --pyproject runtime/python/pyproject.toml
```

The verifier checks the exact file set, every payload checksum, package checksums, commit/version binding, dependency lock, hash-locked requirements rendering, SBOM identity, provenance identities, in-toto subjects, and optional changelog hash.

The verifier rejects extra files and symlinks. This prevents a candidate from quietly acquiring unreviewed payload after approval.

## Candidate contents

A current candidate contains:

```text
release-candidate/
  artifacts/
    manager_reference_runtime-<version>-py3-none-any.whl
    manager_reference_runtime-<version>.tar.gz
  dependencies/
    <exact resolved dependency wheels>
  SHA256SUMS
  dependencies.lock.json
  requirements.lock
  sbom.cdx.json
  provenance.json
  provenance.intoto.jsonl
  candidate-manifest.json
```

The candidate-manifest SHA-256 is the single approval fingerprint. Any change to an artifact, dependency wheel, SBOM, provenance, lock, requirements file, or checksum file changes that fingerprint and makes a prior approval stale.

## Release authorization and trusted publishing

`.github/workflows/release-publish.yml` is prepared but fail-closed by default. It does not run automatically.

A future publication requires all of the following:

- an exact candidate workflow run ID;
- exact commit SHA;
- exact package version;
- exact reviewed candidate-manifest SHA-256;
- exact changelog SHA-256;
- explicit destination `pypi`;
- the exact commit still being current `main`;
- `public-safety` succeeding on that exact commit;
- repository variable `MANAGER_PYPI_TRUSTED_PUBLISHING_ENABLED=true`;
- approval of the protected GitHub environment `pypi-release`;
- a PyPI trusted-publisher configuration matching this repository, workflow, and environment.

The workflow re-downloads and re-verifies the exact candidate both before the protected-environment approval boundary and immediately before publication. If `main` moves during review, or any bound value changes, publication fails and a fresh decision is required.

The publication job uses OIDC trusted publishing instead of a long-lived PyPI token. Immediately before upload it creates keyless GitHub build-provenance attestations for the package artifacts and the candidate manifest. No private signing key belongs in this repository.

No part of candidate construction publishes anything.

## Vulnerability visibility

`.github/workflows/dependency-vulnerability.yml` runs a pinned `pip-audit` version on a weekly schedule and on manual dispatch. This is intentionally separate from deterministic offline unit tests because vulnerability databases are network-dependent and time-varying.

A vulnerability report is evidence requiring triage. It is not an automatic authority to mutate dependency constraints or publish a release.

## Attack coverage

`runtime/python/tests/test_release_candidate_security.py` covers at least:

- modified wheel;
- modified sdist;
- missing artifact;
- wrong commit;
- version mismatch;
- dependency substitution;
- missing dependency hash;
- candidate change after an approval fingerprint is recorded;
- dirty source tree;
- reproducibility failure;
- deterministic sdist normalization.

## Explicit limits

The current machinery does not claim:

- cross-platform dependency lock equivalence;
- cross-platform byte reproducibility;
- that a vulnerability database is complete or continuously available;
- that GitHub/PyPI external configuration is enabled merely because workflows exist;
- that private vulnerability reporting is enabled until repository settings are changed and verified;
- permission to publish any candidate without the explicit release-authority path above;
- production readiness for any deployment environment.
