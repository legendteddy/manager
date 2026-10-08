# Python reference runtime

This directory contains Manager's first reference runtime.

The runtime is intentionally narrow. It implements a deterministic **control plane** for routing, approval, bounded delegation, public-safety filtering, and routine reconciliation. It does not execute arbitrary domain work, perform external side effects, call model providers, or claim production readiness.

Python is the first reference implementation language. The canonical Manager contracts remain language- and provider-neutral.

## Scope

Implemented:

- direct routing for low-risk routine tasks;
- material-action approval blocking;
- stale-approval rejection;
- authority-preserving specialist handoffs;
- routine authority-first reconciliation;
- prompt-injection resistance at the control layer;
- public/private exclusion decisions for modeled public writes;
- contract-shaped trace, result, approval, handoff, and reconciliation artifacts;
- deterministic execution of the public eval fixtures.

Not implemented:

- model-provider adapters;
- arbitrary tool execution;
- durable state stores;
- external approval persistence;
- production side effects;
- full JSON Schema validation;
- semantic task execution beyond the deterministic control rules above.

## Run the eval suite

From the repository root:

```bash
PYTHONPATH=runtime/python python3 -m manager_runtime.evals evals/cases
```

Run unit tests:

```bash
PYTHONPATH=runtime/python python3 -m unittest discover -s runtime/python/tests -v
```

## Design boundary

The runtime consumes the existing public task/eval fixtures and emits artifacts aligned to the contracts in [`contracts/`](../../contracts/). Provider-specific behavior belongs behind future adapters.

A green deterministic eval run demonstrates only that this reference control plane satisfies the encoded deterministic assertions. It does not establish behavioral parity with the private reference architecture, general reasoning quality, security completeness, deployment readiness, or production suitability.
