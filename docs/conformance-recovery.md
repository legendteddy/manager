# Contract conformance and recovery hardening

## Purpose

Stage 9 tightens the boundary between Manager's canonical contracts and its executable reference runtime.

A green unit test is no longer enough by itself. CI now validates representative runtime artifacts against the public JSON Schema contracts using a full Draft 2020-12 validator.

Stage 9 also makes durable state stricter: corrupted state, impossible transitions, unsupported checkpoint versions, and uncertain external outcomes fail closed.

## Schema conformance

`scripts/schema_conformance.py` validates:

- every `contracts/*.schema.json` document against Draft 2020-12 meta-schema rules;
- every public eval fixture against `eval-case.schema.json`;
- deterministic control-plane traces and results;
- approval, handoff, and reconciliation artifacts;
- model requests and normalized model responses;
- trusted tool definitions, model tool proposals, and tool results;
- bounded-loop outputs;
- durable run states and durable agent-loop checkpoints before and after approval resumption.

The conformance dependency is test-only. The reference runtime keeps its zero-dependency execution path unless the optional `conformance` extra is installed.

## Result-status compatibility

Early deterministic paths emitted `result.status = complete`, while later loop paths emitted `completed`.

The result contract now accepts both during compatibility migration. `completed` is preferred for new loop/runtime paths. Removing the legacy `complete` spelling would be a breaking contract change and must follow the protected-surface process.

## Durable state transitions

The SQLite reference store validates state shape on create, load, and write. It also validates every compare-and-swap transition.

Terminal states cannot silently return to execution. Examples of rejected transitions include:

```text
completed → running
blocked   → executing
cancelled → waiting_approval
```

A `waiting_approval` or `executing` state must contain its pending action. A `recovery_required` state must contain a non-empty recovery reason.

Malformed JSON or malformed persisted state raises `RunStateError` before execution logic receives the state.

## Checkpoint versions

Durable agent-loop checkpoints carry an explicit integer version.

The runtime exposes a migration boundary rather than guessing how unknown persisted versions should behave. Stage 8 introduced public version `1`, so there is no legitimate older public format to fabricate a migration for.

The current rules are:

- version `1` loads directly;
- a future version fails closed when read by an older runtime;
- a future breaking version must add an explicit reviewed migration function before old checkpoints may be upgraded;
- migration must advance versions monotonically and may not silently widen authority or budgets.

## Resolving `recovery_required`

`recovery_required` means Manager cannot prove whether an external effect happened. It is not an invitation to retry.

Resolution requires an explicit evidence record with one of three decisions.

### `confirmed_succeeded`

External evidence confirms the action occurred successfully.

Manager does not execute the tool again. It records a recovered verified tool result. For a durable agent loop, the checkpoint moves to `continuation_ready` so the model may continue from the verified recovered outcome.

### `confirmed_not_executed`

External evidence confirms the prior action did not occur.

Manager still does not execute immediately. It creates a **fresh** tool-request and approval identity and returns to `waiting_approval`. Current scope and target authorization must be re-established first.

The old approval is never resurrected.

### `cancelled`

The operator chooses not to continue the uncertain action.

Manager records the resolution and terminates the run without another tool execution.

## Recovery evidence

Recovery evidence is an explicit external assertion, not model reasoning. The recovery record includes:

- run ID;
- resolution ID;
- decision;
- deciding identity;
- decision timestamp;
- evidence summary;
- optional recovered output, subject to redaction policy.

A model response alone must not be treated as proof that an uncertain external side effect succeeded or failed.

## Limits

Stage 9 does not establish:

- exactly-once provider requests;
- exactly-once external side effects;
- distributed transactions;
- distributed leases or consensus;
- automatic reconciliation with arbitrary external systems;
- encrypted durable state;
- production sandboxing;
- production readiness.

Conformance proves that tested artifacts match their declared contracts. It does not prove that every possible provider, tool, integration, or deployment is safe.
