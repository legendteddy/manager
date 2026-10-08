# Contributing

Manager is an early public foundation. Contributions should make the framework clearer, safer, more testable, or more interoperable without increasing agentic complexity for its own sake.

## Read first

- [`ARCHITECTURE.md`](ARCHITECTURE.md)
- [`GOVERNANCE.md`](GOVERNANCE.md)
- [`SECURITY.md`](SECURITY.md)
- [`docs/threat-model.md`](docs/threat-model.md)
- [`docs/release-readiness.md`](docs/release-readiness.md)
- [`docs/public-private-boundary.md`](docs/public-private-boundary.md)
- [`evals/README.md`](evals/README.md)

## Contribution principles

Prefer changes that are:

- small and reviewable;
- provider-neutral at the canonical layer;
- explicit about authority and state ownership;
- backed by behavioral evals when behavior changes;
- public-safe and synthetic;
- reversible where practical;
- honest about maturity and evidence.

Do not add agents, roles, processes, abstractions, or integrations solely to make the system look more sophisticated.

## Public-safety requirement

Do not submit credentials, secret-adjacent values, non-public personal data, customer/employee data, private operational state, proprietary rules or thresholds, private repository mappings, sensitive traces/prompts, private connected-source contents, or local paths containing sensitive identifiers.

If an example needs realistic structure, use synthetic names, identifiers, values, repositories, and organizations.

## Behavioral changes

When changing routing, approval, reconciliation, security, evaluator, state, or evolution behavior:

1. state the behavior being changed;
2. identify the authority boundary affected;
3. add or update a public-safe eval case when feasible;
4. preserve deterministic gates for critical constraints;
5. document material trade-offs;
6. update the threat model when a trust boundary or material security assumption changes;
7. add a concise `CHANGELOG.md` entry when the change is user-visible;
8. do not weaken a test solely to make an implementation pass.

## Runtime and adapter contributions

Core contracts should remain provider-neutral. Provider, MCP, telemetry, model, tool, state-store, or product-specific integrations should normally sit behind an adapter or explicit integration boundary.

Repository tooling does not establish the canonical runtime language.

The Python convenience surface exported through `manager_runtime.__all__` is the intended package-facing API, but it remains pre-stable. Do not infer compatibility guarantees from internal modules merely because they are importable.

## Release changes

A package version bump, tag, GitHub Release, container, or published distribution is not an ordinary documentation change. Publishing is an external commitment and requires explicit maintainer intent for the exact revision and artifact.

Before proposing a release, follow [`docs/release-readiness.md`](docs/release-readiness.md). Do not create a release artifact merely because CI is green.

## License

Contributions are accepted under the Apache License 2.0 unless explicitly stated otherwise in a compatible contribution mechanism.

## Readiness claims

Do not describe Manager as production-ready, validated, behaviorally equivalent to a private reference, or secure-by-construction unless repository evidence supports that exact claim.
