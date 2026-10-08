# Durable state package

This package contains Manager's reference durable run-store boundary, resumable approval workflow, and durable bounded-agent-loop state machine.

- `base.py` defines the provider-neutral Python `RunStore` protocol and optimistic-concurrency errors.
- `sqlite_store.py` supplies a zero-dependency SQLite reference adapter.
- `approvals.py` checkpoints pending tool approvals and resumes them only after freshness, action identity, tool definition, current authorization, and target revalidation.
- `agent_loop.py` persists bounded-loop phases, budgets, seen-action fingerprints, provider/model identity, allowed-tool definition fingerprints, approval interruptions, and sanitized continuation state.

The SQLite database is application-owned runtime state. Do not commit database files to this public repository.

An interrupted `executing` state is treated as an uncertain external outcome and moves to `recovery_required` rather than being retried automatically.

Durable loop mode is intentionally stricter than the non-durable Stage 7 loop: every consequential tool class must checkpoint approval before execution. Restart preserves the original loop budgets and exact seen-action history rather than creating a fresh execution allowance.

See [`../../../../docs/durable-agent-loop.md`](../../../../docs/durable-agent-loop.md).
