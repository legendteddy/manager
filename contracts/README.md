# Machine-readable contracts

This directory contains provider-neutral JSON Schema contracts for Manager's first executable architecture boundary.

These schemas encode governance that is already documented elsewhere. They do not create new authority, select a runtime language, or claim a production-ready implementation.

## Contracts

- `task.schema.json` describes an incoming task and its initial consequence/materiality classification.
- `handoff.schema.json` bounds delegated work and preserves decision ownership.
- `approval.schema.json` binds human approval to an exact reviewed action and supports stale-approval detection.
- `result.schema.json` standardizes returned findings without transferring decision authority.
- `reconciliation.schema.json` records authoritative-state updates and dependent propagation.
- `trace.schema.json` records observable execution evidence without hidden chain-of-thought.

## Design rules

1. Keep canonical contracts provider-neutral.
2. Prefer explicit authority and ownership fields over inferred permission.
3. Unknown values remain unknown; schemas must not force fabricated operational truth.
4. Provider-specific fields belong behind adapters or extension objects.
5. Sensitive/private payloads should be referenced externally rather than embedded in public examples or traces.
6. Breaking contract changes are material architecture changes and require review against `GOVERNANCE.md` and `docs/protected-surfaces.md`.

Schemas use JSON Schema Draft 2020-12. Repository integrity checks verify that contract files are syntactically valid JSON and expose the required schema metadata. Full semantic validation will be added with the first reference runtime or dedicated schema-validation tooling.
