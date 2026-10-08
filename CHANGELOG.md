# Changelog

Manager has not published a tagged public software release yet. This file records unreleased user-visible framework and reference-runtime changes without implying that a package or release artifact exists.

The format is intentionally simple while the project remains pre-stable. Release entries should be created only when an exact commit and version are approved for publication.

## Unreleased

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