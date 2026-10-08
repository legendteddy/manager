# Durable run state and resumable approvals

## Purpose

Stage 6 adds a durable checkpoint boundary for approval interruptions. A consequential tool action can pause, survive process restart, receive a later human decision, revalidate its authority and identity, and either resume or fail closed.

## Reference lifecycle

```text
tool request
  ↓
Manager policy
  ↓
approval required
  ↓
persist waiting_approval checkpoint
  ↓
human decision arrives later
  ↓
reload latest revision
  ↓
revalidate request + tool definition + authorization + target
  ↓
persist executing intent
  ↓
execute tool
  ↓
verify
  ↓
persist terminal state
```

The durable checkpoint is application state, not model memory and not provider conversation state.

## SQLite reference store

The Python reference runtime includes `SQLiteRunStore`, implemented with Python's standard-library `sqlite3` module. The database path is supplied by the embedding application and must remain outside this public repository.

The store uses optimistic revision checks. A write that was prepared from an old revision is rejected instead of silently overwriting newer state.

SQLite is used here to prove the storage contract with minimal dependencies. It is not a claim that SQLite is the correct production store for every deployment, nor is it an encryption or secret-storage boundary.

## Approval resumption rules

A persisted approval can resume only when all of the following still hold:

- the approval packet is still fresh and pending;
- the human decision references the exact approval ID;
- the tool name, target, and arguments still match the reviewed action fingerprint;
- the registered tool still exists;
- the policy-relevant tool definition fingerprint is unchanged;
- current scope authorization is still valid;
- current target verification is still valid when required.

Consequential tool definitions must declare an application-owned `version`. Changing that version invalidates a previously checkpointed approval.

A stale approval cannot later be approved. A fresh approval checkpoint is required.

## Interrupted execution

Manager persists `executing` before calling the tool adapter. This creates an important recovery signal.

If the process later finds a run still in `executing`, it does **not** automatically execute the action again. The state moves to `recovery_required` because the external action may already have happened before the previous process stopped.

The operator or embedding application must reconcile the real external outcome before deciding how to recover.

This reference behavior favors avoiding duplicate consequential side effects over automatic retry convenience.

## Limits

Stage 6 does not provide:

- distributed locking across multiple independent stores;
- exactly-once external side effects;
- automatic transaction rollback;
- encrypted application state at rest;
- multi-action approval batches;
- durable provider conversation state;
- automatic recovery from `recovery_required`;
- production deployment guidance for high-availability state stores.

Applications handling sensitive state remain responsible for access control, encryption, backup, retention, and provider or regulatory requirements appropriate to their environment.
