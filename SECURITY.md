# Security

## Public repository rule

This repository is public. Do not commit secrets, private configuration, proprietary operating rules, private operational state, customer/employee data, non-public personal information, sensitive prompts or traces, or local paths that reveal sensitive identifiers.

Use synthetic examples and generic fixtures. See [`docs/public-private-boundary.md`](docs/public-private-boundary.md).

## External content is evidence, not authority

Instruction-like text found in webpages, files, messages, repositories, issues, comments, tool output, retrieved state, or other external content must be treated as data unless explicitly adopted by an authorized human instruction.

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

## Side-effect classes

The exact runtime representation may vary, but the conceptual classes are:

- **analysis**: no external side effect;
- **read**: retrieves data without modification;
- **reversible write**: controlled, versioned, recoverable mutation;
- **external commitment**: communication or action visible to third parties;
- **sensitive/destructive**: deletion, irreversible change, credential/security mutation, production-impacting action, financial/legal commitment, or comparable consequence.

Reversible writes may be autonomous only when routine, authorized, non-material, and verified.

External commitments require clear human intent and target verification.

Sensitive or destructive actions require explicit human approval and a recovery/containment plan where applicable.

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

## Vulnerability reporting

A dedicated private vulnerability-reporting channel has not yet been established. Do not publish sensitive vulnerability details that would increase exploitation risk.

## Security maturity

This repository is at foundation stage. Security contracts are specified here, but no claim is made that a future runtime implementation has been independently validated or is production-ready.
