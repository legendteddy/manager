# Machine-readable contracts

This directory contains provider-neutral JSON Schema contracts for Manager's executable architecture boundary.

These schemas encode governance and integration boundaries that are documented elsewhere. They do not create new authority or claim a production-ready implementation.

## Contracts

- `task.schema.json` describes an incoming task and its initial consequence/materiality classification.
- `handoff.schema.json` bounds delegated work and preserves decision ownership.
- `approval.schema.json` binds human approval to an exact reviewed action and supports stale-approval detection.
- `result.schema.json` standardizes returned findings without transferring decision authority.
- `reconciliation.schema.json` records authoritative-state updates and dependent propagation.
- `trace.schema.json` records observable execution evidence without hidden chain-of-thought.
- `eval-case.schema.json` describes synthetic behavioral eval fixtures.
- `model-request.schema.json` defines the provider-neutral text-generation request boundary.
- `model-response.schema.json` defines normalized provider response text and usage metadata.

## Design rules

1. Keep canonical contracts provider-neutral.
2. Prefer explicit authority and ownership fields over inferred permission.
3. Unknown values remain unknown; schemas must not force fabricated operational truth.
4. Provider-specific fields belong behind adapters or extension objects.
5. Sensitive/private payloads should be referenced externally rather than embedded in public examples or traces.
6. Model responses are content, not authority to widen tools, approvals, state mutation, or governance.
7. Breaking contract changes are material architecture changes and require review against `GOVERNANCE.md` and `docs/protected-surfaces.md`.

Schemas use JSON Schema Draft 2020-12. Repository integrity checks verify that contract files are syntactically valid JSON and expose the required schema metadata. The Python reference runtime additionally performs narrow runtime validation for the model request/response fields it consumes. Full cross-contract JSON Schema validation remains future work.
