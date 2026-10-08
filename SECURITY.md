# Security

## Public repository rule

This repository is public. Do not commit secrets, private configuration, proprietary operating rules, private operational state, customer/employee data, non-public personal information, sensitive prompts or traces, or local paths that reveal sensitive identifiers.

Use synthetic examples and generic fixtures. See [`docs/public-private-boundary.md`](docs/public-private-boundary.md).

## External content is evidence, not authority

Instruction-like text found in webpages, files, messages, repositories, issues, comments, tool output, retrieved state, model output, MCP discovery metadata, or other external content must be treated as data unless explicitly adopted by an authorized human instruction.

External content must not redefine task scope, authority, approval requirements, security boundaries, tool permissions, secret handling, or repository governance.

## Prompt-injection resistance

When processing untrusted content:

1. separate trusted instructions from content being analyzed;
2. reject embedded attempts to reveal secrets, alter permissions, change goals, or trigger unrelated actions;
3. pass structured facts rather than raw instruction-like text between privileged stages when practical;
4. validate side-effecting tool arguments at the execution boundary;
5. use independent control for high-consequence actions when justified;
6. fail closed when target, scope, authority, or materiality remains materially ambiguous.

## Least-necessary access

A capability receives only the data and tools required for its bounded task.

Research-only work should not receive write authority without need. Evaluators should not receive production write access merely to inspect results. Specialists should not receive sensitive datasets unrelated to their question.

Tool exposure is also an access decision. The embedding application should offer the model only the smallest tool set required for the task.

## Side-effect classes

The conceptual classes are:

- **analysis**: no external side effect;
- **read**: retrieves data without modification;
- **reversible write**: controlled, versioned, recoverable mutation;
- **external commitment**: communication or action visible to third parties;
- **sensitive/destructive**: deletion, irreversible change, credential/security mutation, production-impacting action, financial/legal commitment, or comparable consequence.

Reversible writes may be autonomous only when routine, authorized, non-material, and verified.

External commitments require clear human intent and target verification.

Sensitive or destructive actions require explicit human approval and a recovery/containment plan where applicable.

## Governed tool execution

A model or agent may propose a tool call, but a proposal is never execution authority.

Trusted tool metadata must come from the application-owned registry. In particular, the proposer must not control:

- side-effect class;
- verification requirement;
- tool version;
- authorization scope;
- human-intent confirmation;
- target verification;
- approval status.

The reference runtime binds sensitive/destructive approval to the exact tool name, target, and arguments. If those change, the approval is stale.

Consequential tools must declare an application-owned version and require post-execution verification in the reference runtime. Changing policy-relevant tool metadata invalidates a durable approval checkpoint before resumption.

Provider-executed built-in tools or provider-managed MCP tools are not enabled in the reference path. Custom function calls are normalized into proposals so Manager can evaluate them before application-owned execution. Future provider-managed tool support must preserve an equivalent policy and approval boundary.

## MCP interoperability

MCP is treated as an external capability protocol, not a trusted authority source.

The reference path uses explicit local bindings. An MCP server may advertise a tool name, description, annotations, input schema, and other metadata, but Manager accepts the tool only when an application-owned binding already specifies the exact remote tool identity and trusted local definition.

Remote descriptions, titles, annotations, read-only/destructive hints, and other server metadata must not determine Manager's side-effect class, authorization, approval policy, verifier, sensitive-output policy, or model-facing description.

The discovered input schema must exactly match the reviewed local schema. Schema drift, duplicate discovery names, missing tools, or a changed configured server identity fail closed.

Registration-time validation is not treated as permanent trust. Immediately before execution, the MCP adapter re-discovers the selected tool and revalidates server identity, exact tool presence, and the reviewed schema fingerprint. Drift stops before the remote tool call.

MCP provenance is included in the effective registered tool version. Durable checkpoints therefore reject a resumed consequential action if its configured server, remote tool identity, or discovered input schema has changed since review.

Consequential MCP tools require an application-owned verifier before registration. A successful MCP response is not, by itself, independent verification that the intended external effect occurred.

The optional official SDK bridge may be configured with a positive operation timeout. Timeouts, nested SDK task-group failures, transport failures, and MCP error results are normalized at Manager's adapter boundary. Each reference operation owns a fresh SDK client context so a later operation reconnects rather than reusing an uncertain cancelled session.

CI exercises this behavior against a synthetic local stdio MCP subprocess. The suite verifies discovery, governed execution, result normalization, execution-time schema drift blocking, disappearance/reconnect behavior, error-result normalization, slow-call cancellation, subprocess cleanup, and post-timeout reconnection. It uses no external MCP service, credential, or real side effect.

This evidence does **not** establish Streamable HTTP conformance, production OAuth or credential handling, connection pooling safety, arbitrary-server trustworthiness, hostile-wire robustness, process sandboxing, or production readiness.

Connection targets, process commands, URLs, credentials, OAuth configuration, and other environment-specific MCP settings remain outside canonical public contracts. Tool output returned by MCP is untrusted data and follows the same redaction, continuation, and trace-minimization rules as native tool output.

See [`docs/mcp-adapters.md`](docs/mcp-adapters.md) and [`docs/mcp-transport-conformance.md`](docs/mcp-transport-conformance.md).

## Bounded agent loops

Multi-step execution is a fresh policy decision at every step, not a standing grant of autonomy.

The reference loop requires finite model-step and tool-call budgets, blocks exact repeated tool actions before re-execution, and refuses to partially execute a provider batch that exceeds the remaining tool-call budget.

Approval is not reusable loop state. Any approval object supplied in reusable authorization context is discarded before a newly proposed action is evaluated. A new consequential action must satisfy its own current authorization and approval requirements.

Only executed tool results are eligible for continuation. Outputs marked sensitive by the trusted registry are withheld from the model, and non-sensitive serialized results are bounded in size before provider continuation.

Loop termination does not prove the external objective succeeded. Budget exhaustion, policy blocking, approval interruption, provider incompleteness, execution failure, and loop detection are explicit stop conditions.

## Durable loop state

Durability does not weaken approval freshness and does not create new authority.

The durable loop persists consumed budgets, exact seen-action fingerprints, provider/model identity, allowed tool names, trusted tool-definition fingerprints, normalized model responses, and sanitized continuation results between durable phases.

Restart does not reset budgets, clear loop-detection history, widen the allowed tool set, or carry an old approval into a new action.

Durable mode is deliberately stricter than the non-durable loop: every `reversible_write`, `external_commitment`, and `sensitive_destructive` proposal must cross a durable approval checkpoint before execution. Current authorization, pending request identity, provider identity, and trusted tool definitions are revalidated on resume.

The SQLite adapter persists execution state but is not an encryption boundary or a secret store. Embedding applications are responsible for appropriate filesystem/database access controls, encryption at rest, backup, retention, and regulatory requirements.

Before a resumed consequential side effect executes, Manager persists `status = executing`. If a later process finds that state without a recorded outcome, it does not retry the external action automatically. The reference runtime changes the run to `recovery_required` so the real external outcome can be reconciled first.

A durable checkpoint does not establish exactly-once provider calls or exactly-once external effects. A process failure between receiving a provider response and persisting the next local checkpoint may require the provider request to be issued again.

See [`docs/durable-agent-loop.md`](docs/durable-agent-loop.md).

## Persisted-state conformance

Persisted state is treated as untrusted runtime input when it is loaded back from storage.

The SQLite reference store validates required fields and normalized statuses on create, load, and compare-and-swap. It rejects malformed JSON, malformed state shapes, revision inconsistencies, and illegal state transitions before execution logic can consume them.

Terminal states cannot be silently resurrected into execution. A persisted `waiting_approval` or `executing` state must retain its pending action, and `recovery_required` must retain a non-empty recovery reason.

Durable agent-loop checkpoints are explicitly versioned. Unknown future versions fail closed. Future breaking checkpoint revisions require a reviewed migration function rather than best-effort interpretation.

## Recovery resolution

`recovery_required` represents an uncertain real-world outcome and must not be resolved by automatic retry or model speculation.

Resolution requires explicit external evidence and one of three decisions:

- `confirmed_succeeded`: record a recovered verified result without executing the tool again; a durable loop may then return to `continuation_ready`;
- `confirmed_not_executed`: create a fresh request and approval identity after current scope/target authorization is re-established;
- `cancelled`: terminate without another tool execution.

The old approval is never revived after a recovery decision. See [`docs/conformance-recovery.md`](docs/conformance-recovery.md).

## Contract conformance

CI uses a full JSON Schema Draft 2020-12 validator as a test-only dependency. It validates all public schemas, eval fixtures, and representative artifacts emitted by deterministic, model-backed, tool, bounded-loop, and durable-loop paths.

Schema conformance is evidence that the tested artifact matches the declared public contract. It is not a general security proof.

## Secrets

Never:

- place passwords, tokens, API keys, private credentials, or secret-adjacent values in the repository;
- use the repository as a secret store;
- copy sensitive data into traces or fixtures for convenience;
- publish private provider or infrastructure configuration.

Secret storage and access mechanisms belong outside the public framework.

## Commit metadata

Contributors should use a public-safe Git identity. Maintainers should prefer GitHub no-reply addresses unless they intentionally choose to publish another address.

Repository integrity checks may detect some high-confidence leakage patterns, but automation does not replace review.

## Tracing and observability

Record enough to reconstruct what happened without recording hidden chain-of-thought or unnecessary sensitive content. Useful traces may include run/correlation ID, selected workflow, activated capabilities and purpose, tool actions and outcomes at a non-sensitive level, approvals, reconciliation classification, verification result, final status, and material uncertainty.

Do not place raw sensitive tool arguments, outputs, durable state snapshots, MCP connection configuration, or model continuation payloads into public traces merely for debugging convenience.

## Vulnerability reporting

A dedicated private vulnerability-reporting channel has not yet been established. Do not publish sensitive vulnerability details that would increase exploitation risk.

## Security maturity

Manager now has executable reference controls for model gating, governed synthetic custom-tool execution, Manager-owned MCP tool binding, execution-time MCP schema/identity revalidation, synthetic stdio MCP transport conformance, bounded MCP operation timeouts and cancellation cleanup, approval fingerprinting, durable approval checkpoints, stale-state rejection, optimistic state revisions, legal state-transition validation, corrupted-state fail-closed handling, recovery-required resolution from explicit evidence, checkpoint-version rejection/migration boundaries, finite multi-step budgets, repeated-action loop detection, sensitive-result withholding, durable bounded-loop resumption across approval interruption, and full contract conformance testing in CI. These controls have not been independently security-audited and do not establish production readiness, automatic trust of MCP servers, Streamable HTTP safety, production OAuth/credential safety, production sandboxing, encrypted state handling, distributed coordination, exactly-once side effects, or safe autonomous production side effects.
