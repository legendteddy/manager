# Durable state package

This package contains Manager's reference durable run-store boundary, resumable approval workflow, durable bounded-agent-loop state machine, and recovery/conformance controls.

- `base.py` defines the provider-neutral Python `RunStore` protocol and optimistic-concurrency errors.
- `sqlite_store.py` supplies a zero-dependency SQLite reference adapter and validates persisted state on create, load, and compare-and-swap.
- `transitions.py` defines legal run-state transitions and rejects terminal-state resurrection.
- `checkpoint_versions.py` defines the explicit durable-checkpoint migration boundary and fails closed on unsupported versions.
- `approvals.py` checkpoints pending tool approvals and resumes them only after freshness, action identity, tool definition, current authorization, and target revalidation.
- `agent_loop.py` persists bounded-loop phases, budgets, seen-action fingerprints, provider/model identity, allowed-tool definition fingerprints, approval interruptions, and sanitized continuation state.
- `recovery.py` resolves `recovery_required` only from explicit external evidence and never blindly retries an uncertain side effect.

The SQLite database is application-owned runtime state. Do not commit database files to this public repository.

An interrupted `executing` state is treated as an uncertain external outcome and moves to `recovery_required` rather than being retried automatically.

Recovery has three explicit outcomes: externally confirmed success, externally confirmed non-execution, or cancellation. Confirmed non-execution creates a fresh request/approval identity; it does not revive the old approval. Confirmed success does not re-run the tool and can return a durable loop to `continuation_ready`.

Durable loop mode is intentionally stricter than the non-durable Stage 7 loop: every consequential tool class must checkpoint approval before execution. Restart preserves the original loop budgets and exact seen-action history rather than creating a fresh execution allowance.

See [`../../../../docs/durable-agent-loop.md`](../../../../docs/durable-agent-loop.md) and [`../../../../docs/conformance-recovery.md`](../../../../docs/conformance-recovery.md).
