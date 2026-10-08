# Distributed state, concurrency, and execution safety

## Purpose

Manager's durable runtime must remain safe when more than one process can observe the same run, when workers stop and resume, and when an external operation finishes at an uncertain point relative to local persistence.

This document describes the executable state-backend contract introduced by Production Worker 02. It records both the guarantees Manager now enforces and the guarantees it deliberately does **not** claim.

## Core invariants

For durable consequential execution:

1. a stale state revision cannot overwrite a newer revision;
2. only the current run lease may perform fenced state transitions;
3. every ownership transfer advances a monotonic fencing token;
4. an expired or superseded fencing token never becomes authoritative again;
5. the original tool `request_id` is the durable operation identity;
6. a lost response does not create a new operation identity;
7. a durable operation already recorded as `started` is not executed again automatically;
8. an adapter or verification failure after execution intent is treated as an uncertain external outcome, not proof that nothing happened;
9. `recovery_required` blocks normal execution until explicit external evidence resolves the ambiguity;
10. recovery resolution is serialized under the same lease/fencing boundary as normal durable execution;
11. approval identity, consumed budgets, seen-action history, and checkpoint revisions remain durable across restart;
12. exactly-once external effects are not claimed without external-system evidence.

## Provider-neutral backend contract

`manager_runtime.state.CoordinatedRunStore` extends ordinary `RunStore` semantics with explicit coordination primitives:

- optimistic compare-and-swap by run revision;
- transactional fenced compare-and-swap;
- lease acquisition;
- lease renewal;
- lease validation;
- lease release;
- monotonically increasing fencing tokens;
- a guarded external-execution window;
- durable operation claims;
- durable operation completion evidence;
- operation lookup for restart/recovery.

A backend advertises these through `RunStoreCapabilities`. Consequential durable execution fails closed when the backend cannot supply the required semantics.

This interface is intended to be implementable by SQLite for local/reference use and by PostgreSQL-class transactional databases or future transactional backends for deployment-specific production use.

## SQLite reference semantics

`SQLiteRunStore` now implements schema version `2`.

It coordinates processes that share the same SQLite database file. It uses:

- `BEGIN IMMEDIATE` for transactional CAS and lease changes;
- a database-authoritative clock for lease expiry checks;
- a durable `manager_run_leases` table;
- a durable `manager_operations` table;
- monotonic fencing-token increments when ownership transfers;
- a local execution guard that retains SQLite writer ownership while a consequential adapter call is in flight.

The execution guard closes an important local race. Without it, worker B could observe `status = executing`, decide worker A was dead, and transition the run to recovery while worker A was still actively performing the external call. With the guard, another process sharing the database file cannot transfer ownership or write recovery state during that in-flight execution window.

### SQLite coordination scope

The advertised coordination scope is:

```text
local_multi_process
```

This is intentionally narrower than `distributed` or `horizontally_scaled`.

SQLite remains useful for:

- local execution;
- integration tests;
- deterministic crash/concurrency tests;
- single-host multi-process coordination where all processes share the same database file and filesystem assumptions are appropriate;
- reference semantics for stronger backend implementations.

SQLite is **not** represented as a universal HA datastore, consensus system, network lock service, or horizontally scaled production backend.

## Lease and fencing semantics

A lease contains:

```text
run_id
owner_id
fencing_token
expires_at_epoch
```

A new lease may be acquired only when there is no unexpired current owner. When ownership transfers after release or expiry, the fencing token increases.

Example:

```text
worker A -> fence 7
worker A expires
worker B -> fence 8
worker A resumes late -> rejected forever as stale fence 7
```

A fence is checked transactionally with fenced state mutation. A stale worker cannot silently write over newer state merely because it resumes later.

### Important external-system boundary

A Manager fencing token protects Manager-owned durable state only if the state backend enforces it.

It does **not** automatically fence an arbitrary external API. Strict external fencing requires the external system to accept and enforce a monotonic fence or an equivalent conditional/idempotent write primitive.

## Durable operation identity

For a consequential approved tool action, Manager uses the original tool `request_id` as the durable operation ID.

The operation record binds:

```text
operation_id
run_id
request_fingerprint
fencing_token
status
result evidence
```

The request fingerprint binds the exact tool name, target, and arguments.

Operation statuses are:

- `started`: execution intent was durably claimed, but a verified outcome is not durable;
- `confirmed`: Manager has a durable verified execution result;
- `not_executed`: Manager has durable evidence that this claimed path did not execute the external effect;
- `outcome_unknown`: the adapter was invoked but the runtime cannot prove the real-world result.

An operation-ID collision with a different run or request fingerprint fails closed.

## Consequential execution protocol

The reference flow is:

```text
load waiting approval
  -> acquire lease
  -> revalidate exact approval/request/tool/authorization
  -> fenced CAS to executing
  -> durably claim operation_id = original request_id
  -> enter guarded execution window
  -> invoke adapter + verifier
  -> record confirmed OR outcome_unknown
  -> fenced CAS final/recovery state
  -> release lease
```

If another worker sees the same operation identity:

- `confirmed`: reuse durable evidence; do not invoke the external tool again;
- `started`: do not execute again; require recovery;
- `outcome_unknown`: do not execute again; require recovery;
- `not_executed`: normal automatic execution still does not revive the old approval; recovery policy determines the next fresh approval identity.

## Crash windows

### Before executing intent commit

No external effect has been attempted. A stale state writer is rejected by revision/fence checks.

### After executing intent, before durable operation claim

The run is `executing`. A later worker cannot prove it is safe to replay and moves through explicit recovery semantics rather than blindly invoking the tool.

### After durable operation claim, before adapter invocation

The durable operation is `started`. Automatic replay is suppressed. This is intentionally conservative: Manager prefers an operator/evidence reconciliation over risking a duplicate consequential effect.

### During or after adapter invocation, before confirmation

The durable operation remains `started` if the worker disappears. A normal adapter or verifier failure is recorded as `outcome_unknown`.

A later worker does not retry automatically.

### After durable confirmed result, before final run-state commit

The confirmed operation record is durable. A later worker can reconstruct local run progress from that evidence without executing the external tool again.

### After final run-state commit, before client response

The completed/recovery state and durable operation evidence survive a lost client response. Retrying the same operation identity does not cause another external execution.

## Recovery

`recovery_required` remains a first-class state, not an error string and not an invitation to retry.

Resolution requires explicit external evidence and one of the existing decisions:

- `confirmed_succeeded`;
- `confirmed_not_executed`;
- `cancelled`.

Recovery resolution now acquires a coordinated lease and performs its state change with a fencing token. Competing recovery decisions therefore cannot both become authoritative.

`confirmed_not_executed` creates a fresh request and approval identity. It does not revive the old approval and does not execute immediately.

## Schema migration

SQLite durable-state schema versions are tracked in:

```text
manager_state_meta
```

Version `1` is the historical `manager_runs`-only layout.

Version `2` adds:

```text
manager_run_leases
manager_operations
```

The v1 -> v2 migration is executed transactionally on store initialization.

Rules:

- existing pre-versioned `manager_runs` databases are recognized as v1;
- migration advances monotonically to v2;
- unknown newer schema versions fail closed;
- missing/corrupted migration metadata fails closed;
- incomplete expected schema fails closed;
- no best-effort downgrade is attempted.

Rollback to code that understands only the old layout is not presented as a supported migration strategy. Operators should use backup/restore or a reviewed forward migration plan appropriate to their deployment.

## Tested concurrency and failure cases

The reference suite includes deterministic coverage for:

- stale revision rejection;
- active lease exclusivity;
- ownership transfer with a higher fencing token;
- expired/stale fencing-token rejection;
- durable operation duplicate suppression;
- operation-identity collision rejection;
- live executor versus second-worker takeover attempt;
- crash after an external effect but before confirmation;
- adapter failure after execution intent;
- preservation of `outcome_unknown`;
- restart with a `started` operation;
- no automatic retry from uncertain evidence;
- v1 -> v2 migration;
- rejection of unknown future SQLite schema versions;
- existing recovery and checkpoint conformance tests.

Repository CI exercises the runtime on the supported Python matrix and repeats the full unit/eval/schema/package/transport gates.

## What is guaranteed by the current reference implementation

Within the documented SQLite coordination scope, when callers use the durable coordinated execution path:

- stale revisions cannot overwrite newer revisions;
- an active execution guard prevents a second local process from taking ownership during a consequential adapter call;
- ownership transfer permanently fences older tokens from Manager state mutation;
- duplicate operation identity survives process restart;
- known confirmed operation evidence can be reused without repeating the tool call;
- unknown operation outcomes remain explicit and block automatic replay;
- competing recovery resolutions are serialized;
- schema migration state is versioned and future versions fail closed.

## What is NOT guaranteed

The current implementation does **not** establish:

- exactly-once external side effects;
- exactly-once provider/model requests;
- exactly-once message delivery;
- distributed consensus;
- linearizability across independent SQLite files;
- safe shared-SQLite operation over arbitrary network filesystems;
- PostgreSQL production readiness merely because an interface exists;
- external fencing when the target system ignores Manager fencing tokens;
- external idempotency when the target system ignores Manager operation IDs;
- atomic transactions spanning Manager's database and an arbitrary external system;
- resolution of an unknown external outcome without external evidence;
- encrypted state at rest;
- HA, backup, disaster recovery, capacity, replication, failover, TLS, credential, or network-policy guarantees.

## Requirements for a horizontally scaled production backend

A production network state backend should provide, at minimum:

1. transactional compare-and-swap or equivalent conditional updates;
2. lease acquisition/renewal/transfer using a database-authoritative time source;
3. monotonic fencing tokens stored transactionally with ownership;
4. fenced writes that reject stale tokens in the same transaction as the state mutation;
5. unique durable operation identities and request fingerprints;
6. transactionally durable operation-outcome records;
7. crash-safe schema migrations with explicit version tracking;
8. appropriate isolation semantics documented and tested under real concurrency;
9. bounded lock/transaction timeouts and observable contention;
10. HA/backup/restore/replication evidence appropriate to the deployment;
11. encryption, access control, credentials, TLS, and network policy owned by the embedding environment;
12. external-system idempotency keys, conditional writes, reconciliation APIs, or fencing support where consequential effects require stronger guarantees.

PostgreSQL-class databases are a natural implementation target, but this repository does not claim a PostgreSQL adapter is production-ready until it exists and passes the same contract tests against real concurrent database sessions.

## Remaining distributed assumptions

The reference runtime still relies on deployment-specific answers for:

- how worker identities are issued and authenticated;
- how long lease TTLs should be for workload latency distributions;
- whether long-running operations require lease heartbeats;
- how a production backend behaves during partitions and failover;
- how provider requests are deduplicated, if at all;
- which external tools support idempotency keys or fencing tokens;
- how recovery evidence is obtained and authorized;
- retention and cleanup policy for historical operation evidence;
- database capacity, monitoring, backup, restore, and disaster recovery.

These are operational requirements, not details Manager should silently invent.
