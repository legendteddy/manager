# Protected Surfaces

## Purpose

Protected surfaces define governance and safety concepts that runtime self-improvement may analyze and recommend changes to, but may not automatically rewrite or weaken.

The protection list is itself protected.

## Protected concepts

At minimum, the following are protected:

- human final authority at material boundaries;
- authority precedence;
- public/private disclosure boundary;
- approval requirements and stale-approval semantics;
- materiality classification rules;
- destructive and sensitive action policy;
- prompt-injection and untrusted-content boundaries;
- secrets, privacy, and least-access controls;
- canonical state-ownership rules;
- reconciliation authority rules;
- eval integrity and blocking safety expectations;
- evolution authority and promotion limits;
- release authority, artifact identity, and stale release-approval semantics;
- security trust boundaries and threat-model assumptions;
- protection-list integrity;
- production-readiness, reference-readiness, and validation claim discipline.

## Promotion rule

An improvement candidate may be automatically promoted only when it is:

- low-risk;
- reversible;
- non-protected;
- routine/non-material;
- inside existing authority;
- supported by relevant evidence;
- passing applicable eval and verification gates.

A candidate touching a protected surface or establishing/changing a material rule becomes `NEEDS_HUMAN_APPROVAL` before canonical mutation.

## Evolvable methods

Examples of normally evolvable non-protected methods include:

- routing heuristics;
- context-loading heuristics;
- handoff formatting;
- stop-condition heuristics;
- retrieval strategy;
- tool-selection tactics within existing permission boundaries;
- evaluator implementation details that do not weaken the evaluation contract;
- compression and cognitive-load heuristics;
- retry/recovery tactics;
- adapter behavior within stable contracts.

## Integrity rule

Evolution may not change a protected rule, its own promotion gate, the threat model, release authority, or the eval judging it merely to obtain a PASS.

Protected surfaces may evolve only through the normal material-change process: investigate, recommend, obtain the required human decision, mutate, reconcile, and verify.
