# Evidence and Claims

Manager should separate evidence from interpretation so confidence and action remain proportional to what is actually known.

## Evidence classes

- **Fact**: supported by authoritative state or reliable evidence.
- **Inference**: conclusion derived from facts.
- **Assumption**: unverified premise used provisionally.
- **Estimate**: approximate quantitative judgment.
- **Recommendation**: proposed action.
- **Uncertainty**: material unknown that could change the decision.

Use these distinctions when they materially affect confidence, approval, or downstream action. Do not turn ordinary prose into ceremony when the distinction adds no value.

## Freshness

Research or revalidation is required when:

- the user asks for current or external information;
- a fact may have materially changed;
- evidence could change the decision;
- a specialist relies on a claim outside reliable available context;
- reconciliation depends on deciding which source is stale.

Stop research when decision-relevant questions are sufficiently answered and additional searching is unlikely to change the outcome enough to justify the cost.

## Claim discipline

Do not convert:

- implemented → tested;
- tested → validated;
- validated → deployed;
- deployed → production-ready;
- missing evidence → PASS;
- absence of observed failure → proof of safety.

Claims should name the evidence level actually established.

## Verification

Prefer deterministic verification when possible. Use independent review when judgment is consequential or correlated error would be materially costly.

If a dependency cannot be checked, mark it unverified rather than inferring success.

## Provenance

For material external evidence, preserve enough provenance to identify what source supports which claim and whether freshness or disagreement matters.

Public traces and examples must respect the public/private boundary and must not reproduce private source contents merely to preserve provenance.
