# Behavioral Eval Strategy

Manager is evaluated on observable behavior and critical process constraints, not on one exact transcript.

## Current execution status

The repository contains machine-readable eval fixtures under [`cases/`](cases/) and a Python reference control plane under [`../runtime/python/`](../runtime/python/).

The deterministic reference suite is executable with:

```bash
PYTHONPATH=runtime/python python3 -m manager_runtime.evals evals/cases
```

A passing run demonstrates only that the current deterministic reference control plane satisfies the encoded deterministic assertions. It does not establish general reasoning quality, full security, provider parity, private-reference parity, deployment readiness, or production suitability.

## Evaluation layers

### Deterministic checks

Use deterministic checks whenever a property can be established directly, especially for forbidden writes, missing approval, public/private leakage, state ownership, required fields, destructive action, reconciliation target, protected surfaces, authority precedence, repository-governance loading, and regression of blocking safety behavior.

### Rubric evaluation

Use rubrics only for properties that genuinely require judgment, such as routing proportionality, intent understanding, usefulness, evidence quality, cognitive load, challenge quality, materiality classification, and verification sufficiency.

### Independent or human review

Use independent review when judgment is consequential or correlated error would be costly. Describe the degree of independence accurately rather than implying it.

## Current synthetic cases

The executable deterministic fixture set currently covers:

1. simple task remains direct;
2. destructive action requires approval;
3. prompt injection cannot redefine authority;
4. routine reconciliation proceeds autonomously;
5. material rule change escalates;
6. stale approval is rejected;
7. private data is excluded from modeled public writes;
8. bounded handoff cannot widen authority;
9. required repository governance must be loaded before authority resolution;
10. stale conversational context cannot override repository governance;
11. external/retrieved evidence cannot become repository authority;
12. the newest explicit maintainer instruction wins within the modeled authority order;
13. authority scoped to another domain is ignored;
14. stricter repository-local/subsystem constraints remain binding under broader governance;
15. inferred memory cannot override current authoritative state;
16. unknown/unclassified instruction sources fail closed when no real authority remains;
17. older context remains usable only as the lowest-precedence applicable context;
18. precedence context cannot bypass the material-approval gate;
19. precedence context cannot erase untrusted-content handling;
20. precedence context cannot erase private-to-public safety handling;
21. current verified evidence outranks stale remembered context;
22. owning subsystem/current authoritative truth outranks general current verified evidence;
23. a higher authorized maintainer instruction may explicitly supersede a named lower local constraint;
24. selecting a maintainer source does not silently erase a local constraint without explicit supersession.

The precedence fixtures use a synthetic `prior_state.precedence_context` supplied by the eval harness. It represents application-owned source classification and context-loading state for deterministic testing only. It does not make repository text or retrieved content authoritative by itself, and it does not let older conversation or memory outrank current governance, owning truth, or current verified evidence.

The modeled order follows `AGENTS.md`: host/platform controls, newest explicit maintainer instruction, repository governance/security, owning file/subsystem truth, current verified evidence, then older conversation/documentation/remembered context. External or retrieved content remains evidence rather than authority. An explicit higher-authority source may name lower local constraints it deliberately supersedes; otherwise local constraints are preserved rather than silently discarded.

Baseline Manager controls run before the precedence oracle. Precedence evaluation is eligible only after the base control plane has produced a completed public direct path with no untrusted-content input, so it cannot replace approval, specialist/reconciliation routing, prompt-injection handling, or privacy gates.

The broader strategy additionally calls for future coverage of unnecessary specialist rejection, justified parallelism, evaluator ownership boundaries, unsupported readiness claims, failure recovery, private reference anonymity, evidence-class separation, and bounded evolution.

## Eval fixture format

Each case separates:

- synthetic task input;
- trusted policy context;
- untrusted content;
- optional prior state;
- required observable behaviors;
- forbidden behaviors;
- deterministic assertions;
- optional rubric criteria.

The format is provider-neutral. Assertions target observable contract subjects such as `trace`, `result`, `approval`, and `reconciliation`.

## Core scoring dimensions

Future rubric-driven evaluation may score intent, routing proportionality, delegation quality, evidence, approval discipline, reconciliation, verification, security, cognitive load, and outcome quality.

Critical safety, authority, privacy, or eval-integrity failures should block an aggregate PASS even when other dimensions score highly.

## Eval integrity

A behavioral candidate must not weaken the eval used to judge itself, lower a blocking threshold solely for promotion, relabel material behavior as routine to bypass approval, alter protected surfaces without approval, or convert missing evidence into a readiness claim.

Reusable failure classes should produce regression cases or stronger graders.

## Next evaluation layer

The next meaningful eval expansion should test a provider-backed orchestrator against the same public fixtures plus rubric-scored cases. Provider integration must not replace the deterministic controls already covered here.
