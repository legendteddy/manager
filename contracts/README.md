# Machine-readable contracts

This directory contains provider-neutral JSON Schema contracts for Manager's executable architecture boundary.

These schemas encode governance and integration boundaries that are documented elsewhere. They do not create new authority or claim a production-ready implementation.

## Contracts

- `task.schema.json` describes an incoming task and its initial consequence/materiality classification.
- `handoff.schema.json` bounds delegated work and preserves decision ownership.
- `approval.schema.json` binds human approval to an exact reviewed action and supports stale-approval detection.
- `approval-decision.schema.json` records an explicit human approval or rejection decision.
- `run-state.schema.json` describes durable execution checkpoints, including approval waits and recovery-required states.
- `result.schema.json` standardizes returned findings without transferring decision authority.
- `reconciliation.schema.json` records authoritative-state updates and dependent propagation.
- `trace.schema.json` records observable execution evidence without hidden chain-of-thought.
- `eval-case.schema.json` describes synthetic behavioral eval fixtures.
- `model-request.schema.json` defines the provider-neutral model request boundary, including optional custom tool definitions.
- `model-response.schema.json` defines normalized provider response text, usage metadata, and optional tool proposals.
- `tool-definition.schema.json` defines trusted registry-owned tool metadata, semantic version, and side-effect class.
- `tool-proposal.schema.json` describes a proposed tool name and arguments without granting execution authority.
- `tool-request.schema.json` binds a proposal to a Manager run for policy evaluation.
- `tool-result.schema.json` records deterministic allow/block/approval/failure outcomes and verification evidence.

## Design rules

1. Keep canonical contracts provider-neutral.
2. Prefer explicit authority and ownership fields over inferred permission.
3. Unknown values remain unknown; schemas must not force fabricated operational truth.
4. Provider-specific fields belong behind adapters or extension objects.
5. Sensitive/private payloads should be referenced externally rather than embedded in public examples or traces.
6. Model responses are content, not authority to widen tools, approvals, state mutation, or governance.
7. A model/tool proposer supplies identity and arguments only; trusted side-effect class, verification requirements, and authorization come from outside the proposal.
8. Sensitive/destructive tool approval binds to the exact tool, target, and arguments. Changed parameters invalidate the prior approval.
9. Consequential tool definitions must be versioned by the application so resumed approvals can detect behavior changes.
10. Durable approval resumption must revalidate action identity, tool definition, current authorization, and target before execution.
11. A run interrupted after durable execution intent is recorded must not be automatically retried when the external outcome is uncertain.
12. Provider-executed tools must not bypass Manager's local policy boundary.
13. Breaking contract changes are material architecture changes and require review against `GOVERNANCE.md` and `docs/protected-surfaces.md`.

Schemas use JSON Schema Draft 2020-12. Repository integrity checks verify that contract files are syntactically valid JSON and expose the required schema metadata. The Python reference runtime additionally performs narrow runtime validation for the model, tool, approval, and state fields it consumes. Full cross-contract JSON Schema validation remains future work.
