# Durable run state and resumable approvals

## Purpose

Stage 6 adds a durable checkpoint boundary for approval interruptions. A consequential tool action can pause, survive process restart, receive a later human decision, revalidate its authority and identity, and either resume or fail closed.

Stage 8 reuses the same run-store contract for a bounded multi-step workflow. The surrounding loop can now persist its budgets, seen-action history, trusted tool-definition fingerprints, provider/model identity, and continuation phase around an approval interruption.

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

For a durable bounded loop, successful approved execution can persist `running` rather than terminal `completed`, then return to a `continuation_ready` loop checkpoint for the next model turn.

The durable checkpoint is application state, not model memory and not provider authority.

## SQLite reference store

The Python reference runtime includes `SQLiteRunStore`, implemented with Python's standard-library `sqlite3` module. The database path is supplied by the embedding application and must remain outside this public repository.

The store uses optimistic revision checks. A write that was prepared from an old revision is rejected instead of silently overwriting newer state.

SQLite is used here to prove the storage contract with minimal dependencies. It is not a claim that SQLite is the correct production store for every deployment, nor is it an encryption or secret-storage boundary.

## Run statuses

The reference run-state contract currently supports:

- `running`;
- `waiting_approval`;
- `executing`;
- `completed`;
- `blocked`;
- `failed`;
- `cancelled`;
- `recovery_required`.

A loop stop such as budget exhaustion or repeated-action detection can therefore remain a durable `blocked` outcome rather than being mislabeled as success or failure.

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

Durable-loop resumption additionally requires the provider identity and the full allowed-tool definition fingerprint set to remain consistent with the stored loop checkpoint.

## Interrupted execution

Manager persists `executing` before calling the tool adapter. This creates an important recovery signal.

If the process later finds a run still in `executing`, it does **not** automatically execute the action again. The state moves to `recovery_required` because the external action may already have happened before the previous process stopped.

The operator or embedding application must reconcile the real external outcome before deciding how to recover.

This reference behavior favors avoiding duplicate consequential side effects over automatic retry convenience.

## Durable bounded-loop phases

Stage 8 stores its workflow-specific checkpoint inside run-state extensions and uses four loop phases:

- `response_ready`;
- `waiting_approval`;
- `continuation_ready`;
- `terminal`.

The loop checkpoint preserves consumed model/tool budgets and exact seen-action fingerprints. Restart therefore does not grant a fresh budget or clear loop-detection history.

See [`durable-agent-loop.md`](durable-agent-loop.md) and [`../contracts/agent-loop-checkpoint.schema.json`](../contracts/agent-loop-checkpoint.schema.json).

## Limits

The reference durable state layer does not provide:

- distributed locking across multiple independent workers;
- exactly-once external side effects;
- exactly-once provider calls;
- automatic transaction rollback;
- encrypted application state at rest;
- multi-action approval batches;
- automatic recovery from `recovery_required`;
- production deployment guidance for high-availability state stores.

Applications handling sensitive state remain responsible for access control, encryption, backup, retention, availability, and provider or regulatory requirements appropriate to their environment.
