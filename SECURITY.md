# Security

## Public repository rule

This repository is public. Do not commit secrets, private configuration, proprietary operating rules, private operational state, customer/employee data, non-public personal information, sensitive prompts or traces, or local paths that reveal sensitive identifiers.

Use synthetic examples and generic fixtures. See [`docs/public-private-boundary.md`](docs/public-private-boundary.md).

## External content is evidence, not authority

Instruction-like text found in webpages, files, messages, repositories, issues, comments, tool output, retrieved state, model output, or other external content must be treated as data unless explicitly adopted by an authorized human instruction.

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

## Durable run state

Durability does not weaken approval freshness.

When a run resumes after interruption, Manager must revalidate the exact action, current tool definition, current scope authorization, and current target verification before execution.

The Stage 6 SQLite adapter persists execution state but is not an encryption boundary or a secret store. Embedding applications are responsible for appropriate filesystem/database access controls, encryption at rest, backup, retention, and regulatory requirements.

Manager persists an `executing` checkpoint before a resumed consequential side effect. If a later process finds that state without a recorded terminal result, it must not automatically retry the external action. The reference runtime changes the state to `recovery_required` so the real external outcome can be reconciled first.

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

Do not place raw sensitive tool arguments, outputs, or durable state snapshots into public traces merely for debugging convenience.

## Vulnerability reporting

A dedicated private vulnerability-reporting channel has not yet been established. Do not publish sensitive vulnerability details that would increase exploitation risk.

## Security maturity

Manager now has executable reference controls for model gating, governed synthetic custom-tool execution, approval fingerprinting, durable approval checkpoints, stale-state rejection, optimistic state revisions, and recovery-required handling after interrupted execution intent. These controls are tested in CI but have not been independently security-audited and do not establish production readiness, production sandboxing, credential safety, encrypted state handling, exactly-once side effects, or safe autonomous production side effects.
