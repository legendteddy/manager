# Deployment reference

This document is the vendor-neutral operational reference for running Manager. It does not declare the repository universally production-ready. Organizations still own identity, TLS/DNS, credentials, network policy, external state backends, provider/MCP policy, incident response, capacity evidence, and operational ownership.

## Current deployment boundary

The Python package now includes a canonical network service command:

```text
manager-service
```

That command starts the durable production gateway described in [`service-runtime.md`](service-runtime.md). The smaller `manager_runtime.service.server` surface remains a reference/control-plane compatibility server and is not the canonical production entrypoint.

The deployment layer provides:

- strict development/testing/staging/production configuration;
- fail-fast validation of unknown `MANAGER_DEPLOY_*` settings;
- a production HTTP gateway with separate `MANAGER_SERVICE_*` validation;
- liveness/readiness and graceful-drain primitives;
- bounded concurrency, request ceilings, and timeout budgets;
- single-instance coordinated SQLite durability and a separate network-idempotency ledger;
- SQLite online backup, manifest verification, and restore verification;
- a non-root, read-only-root-compatible container build path;
- deployment CLI operations for configuration and backup/restore.

It does not embed provider or MCP credentials, organization RBAC rules, domain names, certificate issuers, cloud-vendor resources, or a horizontally scalable state backend.

## Production service construction

In staging and production, `manager-service` fails before accepting work unless bearer authentication is enabled and readable and `MANAGER_SERVICE_BACKEND_FACTORY=module.path:callable` is configured.

The backend factory receives `(DeploymentConfig, GatewaySettings)` and is responsible for constructing application-owned trusted runtime objects such as:

- the approved model adapter and model identity;
- the trusted `ToolRegistry` and allowed tool set;
- the coordinated durable `RunStore`;
- reviewed MCP bindings and connection configuration;
- server-side authorization resolution.

Clients cannot send these trust decisions through the HTTP API. The reference `DurableRuntimeBackend` can wrap trusted objects once an embedding application has created them.

The service intentionally does not expose raw MCP server methods and does not expose SSE/streaming. Those remain separate protocol/security decisions.

## Configuration profiles

`manager-deployment validate-config` loads an optional JSON file followed by `MANAGER_DEPLOY_*` environment overrides. Unknown deployment-prefixed settings fail closed. `manager-service` applies the same rule to `MANAGER_SERVICE_*` settings.

Development/testing may use ephemeral settings. Staging and production must explicitly provide operationally significant configuration so changing only the environment name cannot silently inherit unsafe development defaults.

Production reference settings require:

- durable SQLite state for the single-instance reference deployment;
- one service instance only while SQLite owns coordinated state;
- bounded concurrency and queue sizes;
- explicit request/provider/MCP/shutdown timeouts;
- direct TLS or externally terminated TLS;
- operational telemetry enabled;
- an explicit read-only-root contract;
- absolute data, temporary, state, and API-idempotency paths;
- bearer service authentication;
- an application-owned service backend factory.

Memory state and unauthenticated service mode are rejected in staging/production.

## Secret injection

Secrets remain outside canonical deployment configuration. Mount or inject them using the surrounding platform's secret mechanism. Generic examples include read-only files under `/run/secrets`, environment values injected by a trusted secret manager, or workload identity mechanisms that exchange short-lived credentials.

Do not commit secret values, copy them into images/manifests, or emit them through configuration/status output. The reference service bearer token is read from the deployment `secrets_dir` and can be rotated without changing the stable service principal identity.

Provider/MCP credential and OAuth policy belongs to the backend factory/application integration, not to public HTTP request bodies.

## Container and TLS assumptions

`deploy/Dockerfile` uses a build stage and a minimal non-root runtime stage. The application artifact should be immutable and persistent state should remain outside the image. A production release should also bind its base image to an organization-approved immutable digest rather than treating a mutable tag as reproducibility evidence.

Supported TLS modes are:

- `external`: recommended generic profile behind an organization-owned TLS ingress/reverse proxy;
- `direct`: Manager wraps the listening socket with the configured certificate/key;
- `off`: development/testing only.

TLS does not replace authentication or workload identity.

## Health integration

The production service exposes:

- `GET /livez`: process liveness;
- `GET /readyz`: whether Manager can safely accept new work;
- `GET /healthz`: readiness alias.

Readiness stays false during startup, becomes true only after required service dependencies are usable, and becomes false immediately when graceful drain begins. Critical authentication, backend, durable-state, or API-idempotency failures must prevent safe acceptance.

Capacity pressure is surfaced diagnostically without declaring the process dead. Provider/MCP reachability is not automatically a universal liveness dependency; a deployment-specific backend can make it readiness-critical when necessary for safe work.

## Request, timeout, and overload budgets

Keep every network and dependency boundary finite. The deployment request timeout is the amount of time an HTTP caller waits for a newly accepted mutation, not a cancellation lease on the underlying work.

Once a mutation has a durable service idempotency claim, a request timeout or client disconnect does not automatically cancel it. The client may receive `202 in_progress` and poll the durable run. The operation remains bounded by Manager's provider, MCP, tool, state, and service shutdown semantics.

Provider and MCP timeout settings must be applied when the backend factory constructs those adapters. Overload must reject/defer work rather than permit unbounded memory, thread, connection, provider-quota, or database growth.

## Graceful shutdown

On `SIGTERM`/`SIGINT`:

1. readiness becomes false immediately;
2. new work stops being accepted;
3. already accepted HTTP/service work drains only within `graceful_shutdown_seconds`;
4. safe-point cancellation rules remain unchanged;
5. unfinished network mutations become `ambiguous` in the API-idempotency ledger rather than being auto-retried;
6. durable `executing`/`recovery_required` semantics continue to govern potentially consequential external effects.

Set the platform termination grace period above Manager's configured graceful-shutdown budget. `SIGKILL` cannot be handled; recovery after a hard kill relies on durable evidence and reconciliation, not signal hooks.

## SQLite state and network idempotency

The reference deployment is single-instance and uses SQLite for durable run state. This is appropriate only when one service instance and the underlying storage availability satisfy deployment requirements.

Do not use the reference SQLite profile for multiple active instances, cross-host coordination, storage-level high availability, or a shared network filesystem presented as distributed safety.

The service network-idempotency ledger is a separate logical ledger and may share the same SQLite database file. It records request identities before backend work, suppresses duplicate network submissions, replays completed safe responses, and converts orphaned `in_progress` records to `ambiguous` on startup.

This ledger does not make provider calls or arbitrary external side effects exactly once.

A horizontally scaled deployment requires a distributed implementation that preserves, transactionally where required:

- revision CAS;
- leases and fencing;
- execution guards;
- durable operation idempotency;
- atomic recovery resolution;
- equivalent service request idempotency/ownership isolation.

## Backup and restore

With the reference SQLite deployment, use the deployment backup API instead of copying a live database file ad hoc:

```text
manager-deployment backup /var/lib/manager/state/manager.sqlite3 /backup/manager-YYYYMMDD.sqlite3
manager-deployment verify-backup /backup/manager-YYYYMMDD.sqlite3
```

The manifest records SHA-256, size, SQLite user version, table list, and schema fingerprint. It detects corruption/change but is not a cryptographic authenticity signature.

Restore drills should stop writers, verify the selected backup, restore into a separate path, start a disposable process against the restored database, exercise state/service smoke tests, and only then consider replacing production state. Backup retention, encryption, remote copies, and RPO/RTO remain organization-specific.

## Upgrades

Recommended order:

1. verify the exact release candidate and migration notes;
2. take and verify a backup;
3. withdraw readiness and drain the old process;
4. run any reviewed one-way migration exactly once;
5. start the new version with readiness false;
6. verify durable state/checkpoint compatibility, service idempotency state, configuration, and critical dependencies;
7. run smoke tests before readiness is enabled;
8. preserve the previous artifact until rollback eligibility is understood.

Mixed-version safety is not assumed. The single-instance SQLite deployment does not claim rolling-upgrade compatibility.

## Rollback

Application rollback is safe only when the old binary can read current persisted state and no one-way schema/checkpoint/API-idempotency migration or external semantic change has occurred.

If the old version cannot understand new state, rollback may require restoring a verified pre-upgrade backup. That can discard post-upgrade local state, so consequential external outcomes must be reconciled before restore. Never advertise one-click rollback without release-specific evidence.

## Capacity guidance

The repository's example ceilings are starting points, not universal production sizing. Measure actual workload and tune from evidence. Keep process/file-descriptor/thread/connection/run queues bounded and ensure upstream ingress has compatible request/header/body/time limits.

Operational load testing should include saturation, slow clients, dependency latency, state lock pressure, provider/MCP failures, restart during accepted work, and termination during approval/execution/recovery phases.

## Disaster behavior

| Scenario | Expected reference behavior |
| --- | --- |
| Container killed mid-run | Do not blindly retry an uncertain effect. Durable execution/recovery state and API orphan classification drive reconciliation. |
| Client loses response | Retry with the same idempotency key or query the run; the lost connection itself does not cancel work. |
| Host restart | Persistent state survives; orphaned network `in_progress` requests become ambiguous before safe retry. |
| Database unavailable | Readiness false for state-dependent service paths; do not accept work that cannot be durably recorded. |
| Provider unavailable | Fail/decline affected provider work without changing Manager authority or fabricating success. |
| MCP unavailable | Fail affected calls closed; transport failure is not execution success. |
| Disk full | State/idempotency writes or backups fail; readiness should be withdrawn when safe durability is unavailable. |
| Incorrect config | Startup fails before accepting work. |
| Missing service secret/backend | Staging/production startup fails closed. |
| Schema/checkpoint mismatch | Fail closed and use a reviewed migration/recovery procedure. |
| Partial rollout / old-new overlap | Not supported by the single-instance SQLite reference deployment. |

## External production decisions and residual risk

The repository intentionally does not choose:

- cloud/container vendor or private topology;
- DNS names or certificate issuer/enterprise TLS policy;
- production end-user/workload identity and RBAC mechanism;
- provider/MCP credentials, OAuth policy, or enterprise egress/proxy controls;
- telemetry storage, alert routing, SLOs, and on-call ownership;
- backup retention/encryption provider;
- a distributed state/idempotency backend;
- raw MCP server exposure;
- a streaming/SSE event contract.

Those choices must be made by the deploying organization and bound to its own threat model. The repository worker does not deploy production.
