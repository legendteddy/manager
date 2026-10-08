# Behavioral Eval Strategy

Manager is evaluated on observable behavior and critical process constraints, not on one exact transcript.

The repository now includes machine-readable synthetic eval cases under `evals/cases/`. They encode expected and forbidden observable behavior plus deterministic assertions against Manager contracts. They are fixtures, not proof that a runtime already satisfies them.

## Machine-readable cases

Each case follows `contracts/eval-case.schema.json` and contains:

- synthetic task input;
- trusted policy context;
- optional untrusted content;
- optional prior state;
- required observable behaviors;
- forbidden observable behaviors;
- whether approval or human escalation is required;
- deterministic assertions against `trace`, `result`, `approval`, or `reconciliation` outputs.

Repository integrity tooling checks fixture syntax and required structural invariants. Full behavioral execution begins only when a reference runtime or compatible adapter can produce the relevant contract outputs.

Current executable fixtures cover:

1. simple task remains direct;
2. destructive action requires approval;
3. prompt injection cannot redefine authority;
4. routine reconciliation proceeds autonomously;
5. material rule change escalates;
6. stale approval is rejected;
7. private data is blocked from public writes;
8. bounded handoff cannot widen authority.

## Evaluation layers

### Deterministic checks
Use deterministic checks whenever a property can be established directly, especially for forbidden writes, missing approval, public/private leakage, state ownership, required fields, destructive action, reconciliation target, protected surfaces, and regression of blocking safety behavior.

### Rubric evaluation
Use rubrics only for properties that genuinely require judgment, such as routing proportionality, intent understanding, usefulness, evidence quality, cognitive load, challenge quality, materiality classification, and verification sufficiency.

### Independent or human review
Use independent review when judgment is consequential or correlated error would be costly. Describe the degree of independence accurately rather than implying it.

## Behavioral coverage backlog

The machine-readable set should expand to cover the remaining documented behaviors as the runtime boundary becomes executable:

- unnecessary specialist invocation is rejected;
- specialist delegation when materially useful;
- parallelization only when justified;
- evaluator does not seize domain ownership;
- unsupported readiness claim is rejected;
- failure recovery does not fabricate success;
- private reference identity remains private;
- evidence classes remain distinct when material;
- bounded evolution cannot expand authority.

## Core scoring dimensions

A future rubric may score intent, routing proportionality, delegation quality, evidence, approval discipline, reconciliation, verification, security, cognitive load, and outcome quality.

Critical safety, authority, privacy, or eval-integrity failures should block an aggregate PASS even when other dimensions score highly.

## Eval integrity

A behavioral candidate must not weaken the eval used to judge itself, lower a blocking threshold solely for promotion, relabel material behavior as routine to bypass approval, alter protected surfaces without approval, or convert missing evidence into a readiness claim.

Reusable failure classes should produce regression cases or stronger graders.

## Execution status

Current status:

- machine-readable eval schema: implemented;
- synthetic fixtures: implemented;
- repository fixture validation: implemented;
- runtime behavioral execution: not implemented;
- rubric evaluator: not implemented;
- behavioral parity with the private reference architecture: not claimed.

The distinction between fixture existence and runtime validation is intentional.
