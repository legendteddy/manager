# Release candidate build

Manager separates release-candidate construction from publication. Building a candidate is reversible internal preparation; publishing a tag, GitHub Release, package, signed attestation, or other externally distributed artifact remains a separate approval boundary.

## Supported Python baseline

The current Python reference package declares and tests:

- Python 3.11
- Python 3.12
- Python 3.13
- Python 3.14

The package metadata is bounded to `>=3.11,<3.15`. A future Python line is not supported merely because it happens to import successfully.

The required `public-safety` check depends on a compatibility matrix covering every advertised Python line. Each matrix job compiles the runtime, runs the base unit suite and deterministic evals, builds a wheel, installs it into an isolated target, and imports the public package.

## Two dependency lanes

Manager intentionally keeps two dependency-testing modes.

### Compatibility lane

The Python matrix exercises the dependency ranges declared by the package and the runner's compatible packaging environment. This detects when the supported ranges stop working.

### Known-good release-candidate lane

`runtime/python/constraints/known-good.txt` records exact direct versions for the build tools and optional integrations used by release-candidate verification.

This is a known-good direct baseline, not a complete transitive lock. It improves regression diagnosis without falsely claiming that every transitive dependency can be reconstructed offline byte-for-byte from this repository alone.

## Deterministic candidate builder

`scripts/build_release_candidate.py`:

1. verifies the installed direct baseline;
2. derives `SOURCE_DATE_EPOCH` from the exact Git commit;
3. fixes `PYTHONHASHSEED` for the build process;
4. builds one wheel and one source distribution with the pinned build frontend/backend baseline;
5. calculates SHA-256 hashes;
6. writes `SHA256SUMS`;
7. writes `provenance.json` containing the commit, package version, Python/build-tool versions, build epoch, and artifact hashes.

The required CI gate builds the candidate twice from the same commit and compares artifact hashes. A mismatch fails the required `public-safety` check.

`provenance.json` is an inspectable build record. It is not a cryptographic attestation and does not authorize release.

## Manual candidate artifact

The `Build release candidate` workflow is manual-only. It:

- checks out the selected revision;
- uses Python 3.14 as the current release build interpreter;
- installs the known-good direct baseline;
- builds and smoke-tests the candidate;
- uploads the candidate directory as a GitHub Actions artifact retained for seven days.

The workflow has `contents: read` permission only. It cannot create tags, GitHub Releases, packages, attestations, or repository mutations.

## Candidate contents

A candidate bundle contains:

```text
release-candidate/
  artifacts/
    manager_reference_runtime-<version>-py3-none-any.whl
    manager_reference_runtime-<version>.tar.gz
  SHA256SUMS
  provenance.json
```

## Release approval remains separate

Before any public release, verify the exact candidate against `docs/release-readiness.md` and bind approval to at least:

- commit SHA;
- package version;
- artifact filenames and SHA-256 hashes;
- release notes/changelog;
- required CI result;
- known limitations;
- security-reporting path;
- intended publication target.

If any bound value changes, approval is stale and a new release decision is required.

## Explicit non-claims

Stage 14 does not establish:

- a fully hashed transitive lock;
- offline dependency reconstruction;
- cryptographic artifact attestation;
- SBOM publication;
- package-registry trusted publishing;
- release signing;
- production readiness;
- permission to publish the generated candidate.
