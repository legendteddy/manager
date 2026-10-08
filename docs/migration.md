# Migration

## Lineage

```text
Private reference architecture
→ migration evidence

Manager
→ clean public successor candidate
```

The private reference remains private. Its exact repository identity, URL, estate mappings, configuration, real traces, operational state, and domain-specific knowledge are not part of this public repository.

Manager does not become canonical merely because this repository exists.

## Migration principle

Migration is architectural, not a file-for-file rewrite.

Manager should preserve useful behavioral invariants while replacing reference-specific metaphors, fixed-role structure, private configuration, private state, domain mappings, and operational history with generic technical contracts.

The publication classification in [`public-private-boundary.md`](public-private-boundary.md) applies before any concept crosses the boundary.

## Parity areas

Canonical cutover is gated on relevant parity in at least these areas:

| Area | Required property |
| --- | --- |
| Authority | Higher-priority and human approval boundaries remain intact |
| Routing | Simple work remains direct; specialists are proportional |
| Routine/material | Mechanical propagation stays autonomous; consequential rule changes escalate |
| Approval | Missing, stale, or over-broad approval cannot authorize consequential action |
| Security | External content cannot redefine authority; access stays least-necessary |
| Reconciliation | Authoritative state is updated first; required dependencies propagate |
| Evaluator behavior | Evaluators verify without seizing domain ownership |
| State ownership | Authorities and derived consumers remain distinct |
| Public/private isolation | Private configuration/data cannot leak into public artifacts |
| Observability | Actions and decisions are inspectable without hidden reasoning or sensitive logging |
| Bounded evolution | Improvement cannot expand its own authority or weaken gates |
| Resumable approval | Pending consequential actions are rebound and revalidated before execution |
| Protected surfaces | Runtime evolution cannot auto-modify protected governance |
| Failure recovery | Failures preserve known-good state and do not become fabricated success |
| Evidence discipline | Facts, assumptions, estimates, recommendations, and uncertainty are not conflated when material |

## Migration stages

### Stage 0 — Public foundation
Establish language-neutral governance, architecture, security, public/private boundary, reconciliation, bounded handoffs, protected surfaces, synthetic eval strategy, and contribution rules.

### Stage 1 — Contracts
Define machine-readable interfaces for tasks, handoffs, policies, approvals, state, tool actions, traces, eval cases, and adapters.

### Stage 2 — Reference runtime
Select implementation technology from concrete requirements, then implement the smallest useful orchestrator and adapter boundary.

### Stage 3 — Executable evals
Turn synthetic behavioral cases into executable tests with deterministic blocking checks wherever possible.

### Stage 4 — Reference parity
Run representative public-safe scenarios against the private reference behavior and document gaps without publishing private fixtures or topology.

### Stage 5 — Cutover decision
Only after relevant parity is demonstrated should maintainers decide whether Manager becomes canonical.

## Explicit non-goals

- no wholesale copying of private reference repositories;
- no publication of private state, configuration, topology, or real traces;
- no forced preservation of a fixed permanent-agent roster;
- no implementation-language lock solely to create activity;
- no claim that Manager is itself an MCP server or product plugin;
- no production-readiness claim without evidence;
- no canonical cutover by implication.

## Current status

**Stage 0: foundation initialization.** Later stages remain unverified until corresponding artifacts and evidence exist.
