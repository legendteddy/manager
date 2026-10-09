# Parallel Work Coordination

## Purpose

This protocol makes multi-worker repository work inspectable and fail-closed when ownership, write surfaces, dependencies, shared hotspots, or handoffs become ambiguous.

It **does not create authority**. Repository governance remains authoritative, domain decision ownership remains with the declared owner, and the designated integrator owns integration order and implementation selection. The shared coordination issue is the durable coordination surface; Git branches and pull requests are the durable implementation surfaces.

Use this protocol when multiple independent workers operate against the same integration target.

## Core invariant

Every active work package has one worker owner, one bounded scope, one explicit write set, and one return path to the integrator. Workers may discover adjacent defects, but discovery does not transfer ownership.

Parallel work is safe only when all three forms of overlap are explicit:

1. **scope overlap**: two workers are solving the same decision/problem;
2. **write overlap**: two workers may mutate the same path or subtree;
3. **hotspot overlap**: workers touch different files that participate in one fragile shared interface.

A worker must not infer safety merely because filenames differ.

## Durable claim packet

Before the first mutation, post a claim to the coordination issue. A claim should contain:

```yaml
worker_id: "03"
claim_revision: 1
branch: validation/03-parallel-coordination
base_sha: <exact 40-character commit>
mission_id: issue-27
mission_summary: <shared mission in one sentence>
success_criteria:
  - <global condition this package helps satisfy>
integrator: "10"
scope_keys:
  - parallel-coordination
scope:
  - <owned decisions / implementation area>
exclusions:
  - <explicitly non-owned adjacent areas>
write_set:
  - docs/parallel-coordination.md
  - coordination/
  - evals/coordination/
  - runtime/python/tests/test_parallel_coordination.py
hotspots:
  - name: tests
    mode: sequenced
    coordinated_with: ["<peer>"]
dependencies:
  - producer_worker: "<worker>"
    consumer_worker: "03"
    interface: <artifact or invariant required>
    blocking: true|false
    status: pending|ready|consumed|superseded
    producer_head_sha: <exact producer head when a blocking dependency is ready/consumed>
status: declared|active|blocked|ready_for_handoff|superseded|withdrawn
```

`scope_keys` are stable collision keys, not prose summaries. `write_set` is the mutation boundary. Directory entries end in `/` or `/**` and cover descendants. Claims should be narrow enough that another worker or the integrator can compare them without interpreting intent.

## Claim lifecycle and supersession

A claim is versioned by `claim_revision`.

```text
declared -> active -> blocked -> active -> ready_for_handoff
                     \-> superseded / withdrawn
```

When scope, write set, dependency contract, or hotspot handling changes, post the revised claim **before** performing the expanded work. Increment `claim_revision`. Never edit history into ambiguity by silently widening the old packet.

Old claim revisions remain evidence but are no longer the current mutation authority for that work package. Claim-list serialization order carries no authority. Per worker, revision numbers must be unique; at most one claim may be current (`declared`, `active`, `blocked`, or `ready_for_handoff`); and that current claim must be the highest retained revision. Lower revisions are historical (`superseded` or `withdrawn`). A handoff binds to that explicitly resolved current revision, never to whichever claim happens to appear last in a list.

## Collision check before mutation

Immediately before the first mutation, and again before any scope expansion or shared-hotspot edit:

1. read the full coordination issue and newest comments;
2. inspect current worker branches and open PRs;
3. compare active `scope_keys`;
4. compare `write_set` entries for exact or ancestor/descendant overlap;
5. compare shared hotspots even when files are disjoint;
6. reconcile dependencies and changed assumptions;
7. if a collision remains, stop that mutation and post it to the coordination issue.

Routine collision resolution is usually narrowing or sequencing. Architecture/ownership conflicts return to the integrator. A worker does not solve a collision by taking the other scope.

## Shared hotspots

A hotspot is a fragile shared interface or integration surface where disjoint files can still conflict semantically.

Supported coordination modes:

- `exclusive`: one active worker mutates the hotspot; others hand off findings.
- `sequenced`: multiple workers may contribute in an explicit producer/consumer order. Both claims name each other and record the dependency/interface.
- `integrator_only`: workers may analyze or supply patches/tests, but the integrator owns the final hotspot mutation.
- `intentional_verification`: independent overlap is deliberate for Red Team, QA, or comparison. Verification workers remain read-only unless separately assigned a disjoint fix.

Two workers naming the same hotspot without an explicit compatible mode is a coordination failure, not an invitation to race.

## Dependency contract

A cross-worker dependency names:

- producer;
- consumer;
- required artifact/interface/invariant;
- blocking vs non-blocking status;
- state: `pending`, `ready`, `consumed`, or `superseded`;
- for a blocking dependency marked `ready` or `consumed`, the exact producer head used as evidence.

A worker may continue unrelated owned work while a blocking dependency is pending, but it must not present the dependent package as integration-ready. A ready consumer requires the producer to have an unambiguous current claim in `ready_for_handoff`, and its recorded `producer_head_sha` must still match a fresh live observation of the producer branch. An unknown, unfinished, superseded, or advanced producer keeps the consumer non-ready.

If the producer changes the interface or branch head after the consumer has consumed it, the consumer's relevant verification is stale until reconciled.

## Stale-state reconciliation

Re-read live coordination state when any of these triggers occurs:

- `main` or the integration base advances;
- a producer branch/PR advances after being consumed;
- a peer posts a new or revised ownership claim;
- a shared hotspot is newly declared;
- the worker needs to rebase or cherry-pick;
- a PR head changes after tests or evidence were collected;
- a dependency changes from ready to superseded/changed;
- before the final handoff.

A stale base is not automatically wrong. Record one disposition before continuing: `unrelated_reviewed`, `rebase_required`, `integrator_sequence`, or `superseded`.

Evidence is bound to the revision actually tested. If the branch or PR head changes later, do not reuse the prior PASS as proof for the new head. A `ready_for_integration` handoff also requires a fresh live observation of both the branch head and PR head; omitting live head state is not equivalent to proving it unchanged.

## Scope expansion and adjacent defects

When a worker discovers an adjacent defect:

1. identify the existing owner from the coordination issue;
2. post the finding with evidence and a durable reference;
3. continue only within the current claim;
4. expand scope only by posting a revised claim first, and only when the expansion does not seize another worker's ownership.

A final handoff's `changed_files` must be covered by the claim's current `write_set`. An undeclared changed file is evidence of silent scope expansion.

## Out-of-scope finding receipt

An out-of-scope finding is not handed off merely because it appeared in a worker's final prose. Record at least:

```yaml
finding: <concise defect or risk>
owner: "<existing worker/domain owner>"
durable_ref: <coordination issue comment / issue / PR reference>
status: posted|acknowledged
```

The integrator can then account for unacknowledged findings instead of losing them between chats.

## Completion / integrator packet

A worker returning implementation work should provide:

```yaml
worker_id: "03"
claim_revision: 1
mission_id: issue-27
branch: validation/03-parallel-coordination
head_sha: <exact tested head>
status: ready_for_integration
pr: <PR reference>
pr_head_sha: <exact PR head>
summary: <what changed and why>
selection_notes: <what the integrator needs to choose or sequence this safely>
changed_files:
  - <exact paths>
tests:
  - command: <exact command or CI check>
    result: pass|fail
risks: []
assumptions: []
blockers: []
dependencies_consumed: []
out_of_scope_findings: []
```

`ready_for_integration` means the current claim is `ready_for_handoff`, the packet has no unresolved blockers, every reported test in the packet passed, blocking producer dependencies are ready at the exact recorded heads, and the tested head has been reconciled against fresh live branch/PR head observations. A blocked claim, nonempty blocker list, failed test, unfinished producer, or stale dependency head cannot be carried inside a ready packet; use a blocked/non-ready handoff instead. It does **not** mean the integrator must select the implementation or that the system is production-ready.

A blocked return uses the existing bounded-handoff failure fields: `blocked_on`, `why_it_matters`, `what_was_tried`, `smallest_missing_input_or_permission`, and `safe_default_if_any`.

## Integrator responsibilities

The integrator owns:

- integration order;
- comparison of competing/overlapping implementations;
- hotspot collision resolution;
- rejecting stale or evidence-poor handoffs;
- retaining non-duplicative regression tests from rejected implementations when useful;
- final cross-subsystem reconciliation and verification.

Workers should provide enough selection evidence that the integrator does not need to reconstruct their entire chat history.

## Machine-checkable coordination evals

The repository includes an isolated structural validator and synthetic attack fixtures:

```bash
python coordination/validate.py evals/coordination/cases.json
python -m unittest discover -s runtime/python/tests -p 'test_parallel_coordination.py' -v
```

The validator is intentionally narrow. It detects structural coordination hazards such as duplicate scope/write claims, unsafe hotspot overlap, stale or unobserved branch/PR binding, blocked/not-ready completion claims, unresolved or stale blocking dependencies, mission drift, undeclared changed files, failed/missing test evidence, lost out-of-scope findings, and incomplete integrator packets.

It does not decide domain correctness, assign authority, approve scope, or select implementations. Those remain governance/integrator decisions.

## Adversarial checklist

Before calling a parallel protocol change complete, re-run scenarios where:

- two workers claim the same file or scope key;
- a worker silently changes a file outside its write set;
- two disjoint changes collide through a shared hotspot;
- a consumer completes while its producer is unfinished;
- main/branch/PR state changes after evidence was collected;
- a worker's local result no longer matches the shared mission;
- an out-of-scope defect has no durable recipient;
- a worker returns without exact test evidence;
- the integrator cannot tell which head, dependencies, risks, or implementation-selection constraints the worker verified.

Further parallelization has low value when these attacks no longer expose a material routine coordination defect and the live issue state is fully accountable.
