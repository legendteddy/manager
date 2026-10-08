# Changelog

Manager has not published a tagged public software release yet. This file records unreleased user-visible framework and reference-runtime changes without implying that a package or release artifact exists.

The format is intentionally simple while the project remains pre-stable. Release entries should be created only when an exact commit and version are approved for publication.

## Unreleased

### Provider resilience and neutrality

- add explicit provider capability declarations and sanitized normalized provider failure categories;
- add a deterministic synthetic model provider that fully exercises the provider-neutral request/response and continuation boundary without credentials;
- add finite model-call retry policy, explicit request timeouts, and safe initial-turn provider failover that pins the selected route before continuation;
- fail closed on missing response/tool-call identity, malformed arguments, unsupported provider status/reasons, and unexpected response types;
- document durable-provider compatibility rules, unsupported cross-provider continuation, provider failure tests, and residual risks.

### First-release engineering

- add required Python 3.11, 3.12, 3.13, and 3.14 compatibility coverage, including wheel build/install/import on every advertised line;
- bound the current Python package metadata to `>=3.11,<3.15` so future prerelease Python versions are not silently claimed as supported;
- add an exact known-good direct dependency and build-tool baseline while retaining a separate compatibility lane for declared version ranges;
- add local release-candidate construction using commit-derived `SOURCE_DATE_EPOCH`, blocking repeat-build wheel SHA-256 comparison, checksum identity for the source distribution, `SHA256SUMS`, and an inspectable provenance record;
- add a manual read-only release-candidate workflow that uploads a short-lived GitHub Actions artifact without tagging, publishing, attesting, or mutating repository contents;
- document the candidate build contract and the remaining separation between release preparation and release approval.

### Release readiness

- add an explicit release-readiness and pre-stable versioning policy;
- add a repository threat model covering trust boundaries, current mitigations, residual risks, and review triggers;
- modernize Python package metadata and include package-local Apache-2.0 license text;
- add a CI wheel build/install/import smoke test;
- pin the CI runner image family to Ubuntu 24.04 rather than `ubuntu-latest`;
- document remaining blockers before a first public package release.

### MCP transport evidence

- add synthetic local Streamable HTTP MCP conformance alongside stdio conformance;
- verify governed execution, schema-drift blocking, restart/reconnect, timeout behavior, redirect handling, custom synthetic headers, malformed-response failure, and normalized MCP errors;
- preserve meaningful nested MCP transport errors through Manager's boundary.

### Earlier development milestones

Before this changelog was introduced, the repository established machine-readable contracts, deterministic behavioral evals, a Python reference control plane, model and tool adapter boundaries, durable approvals and recovery, bounded resumable agent loops, schema conformance, a governed MCP binding boundary, and synthetic stdio MCP transport conformance.

These milestones are development history, not tagged release records.
