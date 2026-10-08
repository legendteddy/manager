# Durable state package

This package contains Manager's reference durable run-store boundary and resumable approval workflow.

- `base.py` defines the provider-neutral Python `RunStore` protocol and optimistic-concurrency errors.
- `sqlite_store.py` supplies a zero-dependency SQLite reference adapter.
- `approvals.py` checkpoints pending tool approvals and resumes them only after freshness, action identity, tool definition, current authorization, and target revalidation.

The SQLite database is application-owned runtime state. Do not commit database files to this public repository.

An interrupted `executing` state is treated as uncertain external outcome and moves to `recovery_required` rather than being retried automatically.
