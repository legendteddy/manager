# Reconciliation

## Purpose

Reconciliation keeps durable state consistent after a decision or execution changes what is known to be true.

> Update the surface that owns the truth first, then propagate required dependent effects.

## Authority and ownership

A repository, state store, configuration source, schema, or other artifact may own a specific class of truth. Consumers may cache, derive, transform, or reference that truth without becoming authoritative for it.

When ownership is unclear, resolve ownership before mutating multiple surfaces.

## Workflow

```text
detect stale or conflicting state
→ identify the owning authority
→ inspect the smallest sufficient context
→ trace affected dependencies
→ classify routine or material
→ update the owner
→ propagate derived effects
→ verify consistency
→ report residual discrepancy
```

## Routine reconciliation

Proceed autonomously when all of the following hold:

- the governing decision is already confirmed;
- required propagation is unambiguous;
- the work is low-risk and reversible;
- no consequential rule is being established or changed;
- no genuine human preference or cross-domain judgment is required.

Examples include updating stale wording, references, derived documentation, fixtures, tests, generated artifacts, or adapter metadata to match already-confirmed behavior.

## Material reconciliation

Escalate before canonical mutation when reconciliation would establish or change consequential governance, authority, security, privacy, architecture, schema/API, deployment/production behavior, sensitive-data handling, financial/legal commitment, approval boundaries, autonomy boundaries, or another long-lived rule requiring genuine human judgment.

For material reconciliation:

1. investigate the discrepancy;
2. identify the accountable owner of the rule;
3. identify affected dependencies;
4. challenge assumptions when useful;
5. recommend one direction;
6. obtain the smallest necessary human decision;
7. update the authority after approval;
8. propagate dependent effects;
9. verify consistency.

## Reconciliation stewardship

A verifier may own consistency checking and closure without owning the underlying domain truth.

Verification asks: "Did the approved or confirmed truth propagate correctly?" It does not authorize the verifier to invent that truth.

## Dependency propagation

Propagation may include documentation, references, adapters, tests, derived configuration, generated artifacts, caches, or indexes.

Do not duplicate canonical rules across many surfaces without an explicit synchronization contract.

## Verification

After reconciliation, verify:

- the owning source contains the intended truth;
- expected consumers reflect the change;
- no known dependent surface still contradicts the authority;
- no unrelated material state changed;
- claims about completion match the evidence actually checked.

If the environment cannot verify a dependency, mark it unverified instead of manufacturing a PASS.

The Python reference runtime requires a completed receipt for each owner update or consumer
propagation, plus a final verification receipt, before it reports PASS. It structurally checks
these caller-supplied records but cannot authenticate that an external write or check occurred;
production adapters must bind receipts to durable, independently observed evidence.
