# Machine-readable contracts

This directory contains provider-neutral JSON Schema contracts for Manager's executable architecture boundary.

These schemas encode governance and integration boundaries that are documented elsewhere. They do not create new authority or claim a production-ready implementation.

## Contracts

- `task.schema.json` describes an incoming task and its initial consequence/materiality classification.
- `handoff.schema.json` bounds delegated work and preserves decision ownership.
- `approval.schema.json` binds human approval to an exact reviewed action and supports stale-approval detection.
- `approval-decision.schema.json` records an explicit human approval or rejection decision.
- `run-state.schema.json` describes durable execution checkpoints, including approval waits, blocked runs, and recovery-required states.
- `recovery-resolution.schema.json` records explicit evidence-based resolution of an uncertain external outcome.
- `result.schema.json` standardizes returned findings without transferring decision authority.
- `reconciliation.schema.json` records authoritative-state updates and dependent propagation.
- `trace.schema.json` records observable execution evidence without hidden chain-of-thought.
- `eval-case.schema.json` describes synthetic behavioral eval fixtures.
- `agent-loop-policy.schema.json` defines finite model/tool/result budgets plus conservative approval and loop-repetition behavior.
- `agent-loop-checkpoint.schema.json` defines the provider-neutral state needed to resume a bounded loop without resetting budgets, tool identity, or seen-action history.
- `model-request.schema.json` defines the provider-neutral model request boundary, including optional custom tool definitions and verified tool-result continuation envelopes.
- `model-response.schema.json` defines normalized provider response text, usage metadata, and optional tool proposals.
- `tool-definition.schema.json` defines trusted registry-owned tool metadata, semantic version, and side-effect class.
- `tool-proposal.schema.json` describes a proposed tool name and arguments without granting execution authority.
- `tool-request.schema.json` binds a proposal to a Manager run for policy evaluation.
- `tool-result.schema.json` records deterministic allow/block/approval/failure outcomes and verification evidence.
- `mcp-tool-binding.schema.json` binds an explicitly configured remote MCP tool to a trusted local Manager tool definition without transferring policy authority to the MCP server.

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
12. Model/tool continuation must use finite budgets and must route every new proposal through the same policy boundary.
13. Approval is action-specific and must not be carried forward automatically to a later loop action.
14. Only executed and policy-eligible tool results may be returned to a model continuation. Sensitive results must be withheld or safely transformed according to trusted registry metadata.
15. Durable loop resumption must preserve consumed budgets and exact seen-action fingerprints; restart is not a fresh execution budget.
16. Durable mode must revalidate provider identity, allowed tool definitions, the pending request fingerprint, and current authorization before continuing an approved action.
17. Consequential actions in the durable reference path must checkpoint approval before execution so crash recovery cannot blindly replay a side effect.
18. `recovery_required` may be resolved only from explicit external evidence. Confirmed non-execution creates a fresh approval identity rather than reviving the old approval.
19. Persisted state must fail closed on corruption, impossible transitions, or unsupported checkpoint versions.
20. MCP discovery is untrusted capability metadata. Remote descriptions, annotations, and safety hints must not replace Manager-owned tool policy.
21. An MCP binding must be explicit and must fail closed when the configured server/tool identity or discovered input schema drifts from the reviewed local binding.
22. Provider-executed tools must not bypass Manager's local policy boundary.
23. Breaking contract changes are material architecture changes and require review against `GOVERNANCE.md` and `docs/protected-surfaces.md`.

Schemas use JSON Schema Draft 2020-12. CI runs a full Draft 2020-12 validator across every contract, all public eval fixtures, and representative artifacts emitted by the executable reference runtime. Narrow zero-dependency runtime validation still protects hot execution boundaries, while cross-contract conformance is enforced as a dedicated test layer.
