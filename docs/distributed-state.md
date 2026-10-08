# Distributed state, concurrency, and execution safety

## Purpose

Manager's durable runtime must remain safe when multiple processes can observe the same run, workers crash and resume, clients retry, leases transfer, and an external operation completes at an uncertain point relative to local persistence.

This document describes the executable state-backend contract implemented by Production Worker 02. It records both the guarantees Manager enforces and the guarantees it deliberately does **not** claim.

## Core invariants

For durable execution:

1. stale state revisions cannot overwrite newer revisions;
2. an active run lease owns authoritative state mutation for that run;
3. state mutation during an active lease must carry the matching fencing token;
4. every ownership transfer advances a monotonic fencing token;
5. an expired or superseded fencing token never becomes authoritative again;
6. stale release cannot revoke a successor lease;
7. the original consequential tool `request_id` is the durable operation identity;
8. a lost response does not create a new operation identity;
9. operation identity is bound to the exact run and request fingerprint;
10. `started` or `outcome_unknown` operations are not executed again automatically;
11. adapter or verification failure after execution intent is uncertain external outcome evidence, not proof that nothing happened;
12. `recovery_required` blocks normal execution until explicit external evidence resolves the ambiguity;
13. recovery decisions are serialized through the same ownership boundary as normal execution;
14. when an operation ledger row exists, evidence-changing recovery updates the operation row and run row atomically;
15. approval identity, consumed budgets, seen-action history, and checkpoint revisions survive restart;
16. duplicate durable-loop submission cannot silently reset budgets or replace the original run identity;
17. exactly-once external effects are not claimed without external-system evidence.

## Provider-neutral backend contract

`manager_runtime.state.CoordinatedRunStore` extends ordinary `RunStore` semantics with explicit coordination primitives:

- optimistic compare-and-swap by run revision;
- transactional fenced compare-and-swap;
- lease acquisition;
- lease renewal;
- lease validation;
- lease release;
- monotonically increasing fencing tokens;
- a guarded consequential-execution window;
- durable operation claims;
- durable operation completion evidence;
- operation lookup for restart/recovery;
- atomic operation-ledger plus run-state recovery resolution.

Backends advertise these guarantees through `RunStoreCapabilities`. Consequential durable execution fails closed when the store does not expose the required capability set.

The interface is provider-neutral. SQLite supplies a local/reference implementation. A PostgreSQL-class or future transactional backend must implement the same semantics and pass equivalent real-concurrency tests before it may claim the same execution guarantees.

## Durable run ownership

A lease contains:

```text
run_id
owner_id
fencing_token
expires_at_epoch
```

A lease can be acquired only when there is no unexpired current owner. Transfer after release or expiry increases the fencing token.

Example:

```text
worker A -> fence 7
worker A expires or releases
worker B -> fence 8
worker A resumes late -> fence 7 remains stale forever
```

A fence is checked in the same transaction as a fenced state write. A stale worker therefore cannot regain authority merely because it resumes later.

Plain SQLite `compare_and_swap()` is also guarded: while an unexpired lease exists, unfenced CAS is rejected. Code that owns a lease must use `fenced_compare_and_swap()`.

## Durable-loop submission identity

The durable bounded loop now creates a run claim **before the first provider request**.

The initial claim records an `initial_provider` marker containing:

- provider and model identity;
- exposed tool names and trusted tool-definition fingerprints;
- model input;
- model-step budget;
- tool-call budget;
- tool-result continuation bound;
- output-token bound.

It also stores a durable submission fingerprint. A duplicate submission with the same deterministic run identity but different execution-defining inputs or budgets fails closed instead of silently resetting the run.

If the process disappears during the first provider request, the initial claim remains durable and restart uses the same deterministic provider request identity. This prevents two live workers from concurrently owning the same initial durable run.

This does **not** prove exactly-once provider calls. A provider may have accepted a request whose response was lost before Manager persisted it, so a restart can reissue that provider request. Stale provider responses cannot overwrite newer Manager state because lease/fence/revision checks still apply.

## SQLite reference semantics

`SQLiteRunStore` currently exposes schema version `3` and reports coordination scope:

```text
local_multi_process
```

It coordinates processes that share the same SQLite database file. It uses:

- `BEGIN IMMEDIATE` for transactional CAS and lease changes;
- a database-authoritative clock for lease expiry checks;
- `manager_run_leases` for durable ownership/fencing state;
- `manager_operations` for durable consequential-operation evidence;
- a local execution guard that retains SQLite writer ownership while a consequential adapter call is in flight;
- schema-v3 database triggers that reject pre-coordination runtimes attempting to write `manager_runs`;
- schema-shape and trigger-integrity validation on open.

### Why the SQLite execution guard is deliberately coarse

Without an execution guard, worker B could observe `status = executing`, assume worker A was dead, transfer ownership, and begin recovery while worker A was still actively performing the external call.

The SQLite reference implementation prevents that race by retaining writer ownership across the actual consequential adapter/verification call. This serializes SQLite writes more broadly than a network database implementation should need to. It is a correctness-first reference behavior, not a horizontal-scaling recommendation.

### SQLite scope

SQLite remains useful for:

- local execution;
- deterministic integration/concurrency/crash tests;
- single-host multi-process coordination when every process shares the same database file and filesystem assumptions are appropriate;
- reference semantics for stronger transactional backends.

SQLite is **not** represented as a universal HA datastore, consensus system, network lock service, shared-network-filesystem recommendation, or horizontally scaled production backend.

## Mixed-runtime protection

Schema v3 addresses a dangerous rolling-upgrade case.

A pre-v3 Manager runtime understands only `manager_runs`; it does not know leases or fencing. Without a database-level guard, such a process could open an upgraded file and write run state while newer workers believed lease ownership was authoritative.

Every v3 connection created by `SQLiteRunStore` registers:

```text
manager_runtime_schema_version()
```

Schema-v3 `BEFORE INSERT`, `BEFORE UPDATE`, and `BEFORE DELETE` triggers on `manager_runs` invoke that function. Older runtimes do not register it, so their authoritative writes fail at SQLite.

The runtime validates the guard trigger bodies on open. A same-named but ineffective trigger is rejected.

This is a mixed-version safety mechanism for supported Manager runtimes, not a security boundary against an actor with arbitrary database-administration access.

## Durable operation identity

For an approved consequential tool action, Manager uses the original tool `request_id` as the durable operation ID.

The operation record binds:

```text
operation_id
run_id
request_fingerprint
fencing_token
status
result evidence
```

The request fingerprint binds exact tool name, target, and arguments. Cross-run operation-ID reuse or inconsistent confirmed result identity fails closed.

Operation statuses are:

- `started`: execution intent was claimed, but a verified outcome is not durable;
- `confirmed`: Manager has durable verified execution evidence;
- `not_executed`: external evidence proves the claimed prior path did not execute the effect;
- `outcome_unknown`: the adapter was invoked but Manager cannot prove the real-world outcome.

## Consequential execution protocol

The reference flow is:

```text
load waiting approval
  -> acquire lease
  -> revalidate exact approval/request/tool/current authorization
  -> fenced CAS to executing
  -> durably claim operation_id = original request_id
  -> enter guarded execution window
  -> invoke adapter + verifier
  -> persist confirmed OR outcome_unknown evidence
  -> fenced CAS final/recovery state
  -> release lease
```

If another worker encounters the same operation identity:

- `confirmed`: reuse durable evidence; never invoke the external tool again;
- `started`: do not execute again; require reconciliation/recovery;
- `outcome_unknown`: do not execute again; require reconciliation/recovery;
- `not_executed`: the historical operation remains closed; a later retry requires the explicit recovery path and a fresh request/approval identity.

## Crash windows

### Before executing intent commit

No external effect has been attempted. Stale state mutation is rejected by revision/fence checks.

### After executing intent, before durable operation claim

The run is `executing` but Manager cannot prove whether an external attempt began. Restart does not blindly execute. It enters explicit recovery semantics.

### After operation claim, before adapter invocation

The durable operation is `started`. Automatic replay is suppressed. This is intentionally conservative: Manager prefers reconciliation over risking a duplicate consequential effect.

### During or after adapter invocation, before confirmation

A process loss leaves the durable operation `started`. A normal adapter/verification failure is recorded as `outcome_unknown`.

Restart does not retry automatically.

### After confirmed operation evidence, before final run-state commit

The confirmed operation row is durable. Restart can reuse that evidence to repair local run progress without invoking the external tool again.

### After final run-state commit, before client response

Run state and operation evidence survive the lost response. Client retry with the same durable identity does not cause another external tool execution.

## Recovery

`recovery_required` is a first-class safety state. It is not an error string and not permission to retry.

Resolution requires explicit evidence with one of:

- `confirmed_succeeded`;
- `confirmed_not_executed`;
- `cancelled`.

Competing recovery decisions acquire the same run lease, so only one can become authoritative.

When an uncertain durable operation row exists:

- `confirmed_succeeded` atomically changes the operation to `confirmed` and advances the run;
- `confirmed_not_executed` atomically changes the historical operation to `not_executed` and advances the run to a **fresh** request/approval identity;
- `cancelled` terminates the run without rewriting uncertain operation evidence, because cancellation is not proof of the external outcome.

A rollback test verifies that if the run transition fails, the operation-ledger update is rolled back in the same transaction.

Cancellation deliberately does not require the historical tool adapter to remain installed: cancelling executes nothing and must remain possible after tool removal or version change.

## Schema migration

SQLite durable-state versions are tracked in `manager_state_meta`.

### Version 1

Historical `manager_runs`-only layout.

### Version 2

Adds:

```text
manager_run_leases
manager_operations
```

### Version 3

Adds the mixed-runtime `manager_runs` write fence and stronger schema validation.

Migration behavior:

- a pre-versioned historical `manager_runs` database is recognized and advanced through the reviewed migrations;
- v1 → v2 is transactional;
- v2 → v3 is transactional;
- data survives the v2 → v3 upgrade;
- old v2 runtime writes fail after the database reaches v3;
- unknown future schema versions fail closed;
- missing/corrupted version metadata fails closed;
- missing required tables fail closed;
- missing required columns or primary keys fail closed;
- missing run foreign keys fail closed;
- missing or malformed runtime-guard triggers fail closed;
- no best-effort downgrade is attempted.

Rollback to old code is not presented as a supported database downgrade. Use backup/restore or a reviewed forward migration plan appropriate to the deployment.

## Checkpoint compatibility

Durable agent-loop checkpoints retain explicit checkpoint versioning. Unknown future checkpoint versions fail closed; no runtime guesses how to reinterpret authority, budgets, or prior approvals.

The pre-first-provider `initial_provider` marker also carries its own version and is validated before restart.

A future breaking checkpoint format must add an explicit reviewed migration that advances monotonically and does not widen authority or replenish consumed budgets.

## Tested concurrency and failure cases

Deterministic tests now cover, among other cases:

- stale revision rejection;
- active lease exclusivity;
- lease expiry and transfer;
- monotonically increasing fencing tokens;
- stale worker rejection;
- stale lease release unable to revoke a successor;
- unfenced CAS rejected while a lease is active;
- durable operation duplicate suppression;
- cross-run operation-ID collision rejection;
- inconsistent confirmed operation evidence rejection;
- two workers racing one consequential execution;
- duplicate approval/resume while an executor is live;
- duplicate initial durable-loop submission;
- same run identity with changed submission/budget rejected;
- active provider continuation ownership;
- crash during initial provider request;
- crash after external effect but before confirmation;
- adapter/verification failure after execution intent;
- preservation of `outcome_unknown`;
- restart with `started` operation;
- no automatic retry from uncertain evidence;
- atomic operation/run recovery;
- atomic rollback when recovery run transition fails;
- conflicting concurrent recovery decisions;
- cancellation after historical tool removal;
- v1 migration;
- direct v2 → v3 migration with preserved data;
- old-runtime writes rejected after v3 upgrade;
- corrupted schema shape rejected;
- missing/malformed runtime guard rejected;
- unknown future SQLite schema versions rejected;
- existing checkpoint/recovery contract-conformance cases.

Repository CI executes unit tests on Python 3.11, 3.12, 3.13, and 3.14, deterministic behavioral evals, schema/runtime conformance, reproducible wheel builds, and MCP transport conformance.

## Guarantees of the current SQLite reference implementation

Within the documented local/shared-file coordination scope, when callers use the coordinated durable paths:

- stale revisions cannot overwrite newer revisions;
- only the active fence can mutate a leased run;
- a stale release cannot revoke a successor lease;
- a live consequential executor cannot be concurrently taken over through the reference SQLite backend;
- ownership transfer permanently fences older tokens from Manager state mutation;
- durable operation identity survives process restart and client retry;
- confirmed operation evidence is reused without repeating the external tool call;
- unknown operation outcomes remain explicit and block automatic replay;
- recovery decisions are serialized;
- evidence-changing recovery keeps the operation ledger and run state transactionally consistent;
- duplicate durable-loop submissions cannot silently reset persisted execution controls;
- upgraded SQLite files reject writes from the known pre-coordination runtime implementation;
- schema and checkpoint versions fail closed when unsupported or materially corrupted.

## What is NOT guaranteed

The current implementation does **not** establish:

- exactly-once external side effects;
- exactly-once provider/model requests;
- exactly-once message delivery;
- distributed consensus;
- linearizability across independent SQLite files;
- safe shared-SQLite operation over arbitrary network filesystems;
- PostgreSQL production readiness merely because a protocol exists;
- external fencing when the target system ignores Manager fencing tokens;
- external idempotency when the target system ignores Manager operation IDs;
- atomic transactions spanning Manager's database and an arbitrary external system;
- automatic determination of an unknown external outcome;
- authenticated worker identity solely from a caller-provided `owner_id`;
- encrypted state at rest;
- HA, backup, disaster recovery, capacity, replication, failover, TLS, credential, or network-policy guarantees.

Provider/model requests can still be duplicated if a request succeeds externally but the worker loses its response, or if a provider call outlives its lease and another owner resumes. Manager fences stale local state writes but cannot invent provider-side idempotency.

## Requirements for a horizontally scaled production backend

A production network state backend should provide, at minimum:

1. transactional compare-and-swap or equivalent conditional updates;
2. lease acquisition/renewal/transfer using a backend-authoritative time source;
3. monotonic fencing tokens stored transactionally with ownership;
4. fenced writes rejecting stale tokens in the same transaction as mutation;
5. a safe execution-ownership mechanism that does not allow takeover while a consequential call is actively in its uncertain window;
6. unique durable operation identities and request fingerprints;
7. transactionally durable operation-outcome records;
8. atomic operation-ledger/run-state recovery resolution;
9. crash-safe schema migrations with explicit version tracking;
10. reviewed mixed-version deployment behavior;
11. documented/tested isolation semantics under real concurrent sessions;
12. bounded lock/transaction timeouts and observable contention;
13. HA/backup/restore/replication evidence appropriate to the deployment;
14. encryption, access control, credentials, TLS, and network policy owned by the embedding environment;
15. external-system idempotency keys, conditional writes, reconciliation APIs, or fencing support wherever stronger real-world guarantees are required.

PostgreSQL-class databases are a natural implementation target, but this repository does not claim a PostgreSQL adapter is production-ready until one exists and passes the same contract tests against real concurrent database sessions and deployment failure modes.

## Remaining distributed assumptions and infrastructure requirements

The reference runtime still requires deployment-specific answers for:

- authenticated/stable worker identity issuance;
- lease TTL selection for real workload latency distributions;
- heartbeat strategy for long-running provider/read operations;
- behavior under network partitions, failover, replica lag, and clock/leadership changes in the chosen network database;
- provider request idempotency or reconciliation;
- external tool support for idempotency keys, fencing tokens, conditional writes, or authoritative lookup/reconciliation APIs;
- authorization and provenance of recovery evidence;
- retention/cleanup policy for historical operation evidence;
- database capacity, monitoring, backup, restore, disaster recovery, encryption, and access control.

These are explicit operational requirements. Manager does not silently manufacture guarantees for infrastructure or external systems that do not provide them.
