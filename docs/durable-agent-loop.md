# Durable bounded agent loop

## Purpose

Stage 8 connects Manager's durable approval state with the Stage 7 bounded model/tool loop.

The reference path persists enough loop state to resume after an approval interruption without replaying an already-approved side effect.

```text
model response
    ↓
trusted tool policy
    ↓
analysis/read tool
    ↓
checkpoint sanitized result
    ↓
model continuation

or

consequential tool proposal
    ↓
durable approval checkpoint
    ↓
human decision later
    ↓
revalidate exact action + tool definitions + current authorization
    ↓
persist executing intent
    ↓
execute + verify
    ↓
persist continuation-ready state
    ↓
model continuation
```

Durable mode is intentionally stricter than the non-durable Stage 7 loop: every consequential side-effect class must cross a durable approval checkpoint before execution.

## Persisted loop identity

The provider-neutral durable checkpoint records:

- provider identity;
- model identity;
- allowed tool names;
- trusted tool-definition fingerprints;
- model-step budget;
- tool-call budget;
- per-result continuation size bound;
- model steps already consumed;
- tool proposals already processed;
- exact seen-action fingerprints;
- prior model response reference;
- pending proposal identity when approval is required;
- pending request fingerprint;
- normalized current model response or sanitized continuation results for the next phase.

The checkpoint does not transfer authority to the model provider. Current registry metadata and current authorization are revalidated when the run resumes.

## Durable phases

The reference checkpoint uses four phases:

- `response_ready`: a normalized model response has been persisted and its proposals are ready for policy evaluation;
- `waiting_approval`: one serialized consequential action is waiting for an exact human decision;
- `continuation_ready`: verified tool results have been persisted and are ready to return to the model;
- `terminal`: the bounded loop has completed or stopped.

The surrounding run state still uses the broader statuses from `run-state.schema.json`, including `running`, `waiting_approval`, `executing`, `completed`, `blocked`, `failed`, `cancelled`, and `recovery_required`.

## Consequential actions

In durable mode, `reversible_write`, `external_commitment`, and `sensitive_destructive` actions do not execute directly from a model proposal.

Manager first creates an exact approval checkpoint. The checkpoint binds the action to the same trusted identity rules used by the Stage 6 approval runtime:

```text
tool name
+ target
+ arguments
+ trusted tool definition/version
+ current authorization
```

A changed target, arguments, provider, allowed-tool definition, or policy-relevant tool definition prevents normal resumption.

## Crash-after-side-effect behavior

Before an approved consequential action executes, Manager durably writes `status = executing`.

If the process disappears after the external effect but before a terminal result is persisted, a later resume does not retry the action. The run moves to `recovery_required` so the real external outcome can be reconciled first.

This protects against blind duplicate execution. It does **not** establish exactly-once external side effects.

## Read/model phase durability

The reference runtime persists normalized model responses before evaluating their proposals and persists sanitized read/analysis tool results before the next continuation call.

A process restart can therefore continue from the latest durable phase rather than intentionally replaying an approved consequential side effect.

The reference implementation does not claim exactly-once provider calls or globally distributed workflow execution. A provider request may need to be reissued when a process fails between an external model response and the next local checkpoint.

## Loop controls survive restart

Resumption restores the original finite controls rather than granting a fresh budget:

- consumed model steps remain consumed;
- consumed tool calls remain consumed;
- exact seen-action fingerprints remain active;
- the allowed tool set remains fixed;
- trusted tool-definition fingerprints must still match;
- prior approvals are not carried forward to new actions.

This prevents a restart from becoming a budget reset or an approval reset.

## Provider neutrality

The durable checkpoint stores a provider-neutral prior-response reference and normalized continuation payload.

The OpenAI reference adapter may map that to Responses API continuation fields, but the canonical checkpoint does not require OpenAI or any one provider's session format.

## Storage boundary

The first reference store remains SQLite through Python's standard library.

The state database belongs to the embedding application and must remain outside this public repository. Manager does not treat the SQLite reference adapter as:

- an encryption boundary;
- a secret store;
- a high-availability datastore;
- a distributed lock service;
- a regulatory retention system;
- a universal production database recommendation.

Deployments remain responsible for access control, encryption at rest, backup, retention, availability, and regulatory requirements appropriate to their environment.

## Stage 8 limits

Stage 8 does not claim:

- exactly-once external side effects;
- exactly-once provider calls;
- distributed locking or multi-worker lease coordination;
- automatic resolution of `recovery_required` outcomes;
- transaction rollback orchestration;
- production sandbox/process isolation;
- encrypted durable state by default;
- provider failover;
- production credentials or production tool safety;
- private-reference parity;
- production readiness.

The durable loop is a reference control pattern for resumable governed execution, not a claim of unrestricted autonomous operation.
