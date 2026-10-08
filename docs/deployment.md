# Deployment reference

This document is the vendor-neutral operational reference for running Manager. It is deliberately narrower than a claim of universal production readiness. Organizations still own identity, TLS termination, credentials, network policy, external state backends, incident response, and provider/MCP policy.

## Current deployment boundary

The current `main` runtime is a Python reference control plane with a local SQLite durability adapter. It is not yet a horizontally scalable production service. This deployment work therefore provides the reusable operational boundary now and leaves the network-facing service API to the service-runtime owner.

The deployment helpers provide:

- strict development/testing/staging/production configuration;
- fail-fast validation of unknown `MANAGER_DEPLOY_*` settings;
- an explicit single-instance SQLite rule;
- liveness/readiness state primitives for the service layer;
- SIGTERM/SIGINT drain coordination primitives;
- SQLite online backup, manifest verification, and restore verification;
- a non-root, read-only-root-compatible container build path;
- a deployment CLI for configuration and backup/restore operations.

They do **not** store secrets or define provider credentials, MCP credentials, application authentication, domain names, certificate issuers, or cloud-vendor resources.

## Configuration profiles

`manager-deployment validate-config` loads an optional JSON file followed by `MANAGER_DEPLOY_*` environment overrides. Unknown deployment-prefixed settings fail closed.

Development defaults are intentionally local and ephemeral. Staging and production must explicitly provide the operationally important settings, so changing only `MANAGER_DEPLOY_ENV=production` can never silently reuse a development configuration.

Production reference settings require:

- durable SQLite state;
- one instance only;
- bounded concurrency and queue sizes;
- explicit request/provider/MCP/shutdown timeouts;
- direct TLS or externally terminated TLS;
- operational telemetry enabled;
- an explicit read-only-root contract;
- absolute data, temporary, and SQLite paths.

`memory` state is rejected in staging and production. SQLite with `instance_count > 1` is rejected because the current reference store does not provide distributed coordination.

### Secret injection

Secrets remain outside deployment configuration. Mount or inject them using the surrounding platform's secret mechanism and point the provider/MCP/service adapter at those external references. Generic examples include:

- read-only files under `/run/secrets`;
- environment variables injected at process start by a trusted secret manager;
- workload-identity mechanisms that exchange short-lived credentials without storing them in the image.

Do not commit secret values, copy them into the image, place them in Compose manifests, or emit them through `print-effective-config`.

## Container build

`deploy/Dockerfile` uses a build stage and a minimal runtime stage. The runtime stage:

- contains no compiler or build toolchain added by Manager;
- installs only the built Manager wheel, with `--no-deps`;
- runs as UID/GID 10001;
- has explicit writable data/runtime directories;
- is compatible with a read-only root filesystem when those directories are mounted;
- contains no credentials.

The example base image tag is convenient for development. A production release should bind `PYTHON_IMAGE` to an organization-approved immutable image digest and record that digest with the deployment evidence. The repository does not claim byte-reproducible container images from a mutable tag.

The current image defaults to `manager-deployment validate-config`. Once the canonical network service command is merged, bind that command in the deployment manifest and use `manager-deployment probe` against its liveness/readiness endpoints. Do not pretend the config-validation command is a running service healthcheck.

## Health integration

A hosting service should create one `HealthRegistry` and expose two distinct endpoints:

- **liveness**: process can continue running;
- **readiness**: process can safely accept new work.

Readiness must remain false during startup, become true only after required state/config/dependencies are usable, and become false immediately when graceful drain starts. Critical dependency failures must make readiness false. Noncritical telemetry failures may be reported without forcing unready if policy permits.

Do not make provider or MCP availability a universal liveness requirement. Their failure should normally prevent only work that requires them. A deployment may make a dependency readiness-critical when safe execution genuinely depends on it.

## Graceful shutdown

On SIGTERM/SIGINT:

1. stop accepting new work and make readiness false;
2. allow in-flight non-consequential work to finish within the configured drain budget;
3. preserve the runtime's existing durable `executing`/`recovery_required` semantics for consequential work;
4. do not auto-retry uncertain external side effects merely because the process is shutting down;
5. exit before the orchestrator's hard-kill deadline.

Set the platform termination grace period slightly above `graceful_shutdown_seconds`. SIGKILL cannot be handled. Recovery after a hard kill relies on durable-state reconciliation, not signal hooks.

## SQLite state

The local reference deployment uses a persistent SQLite database outside the image. This is appropriate for a single service instance where host/storage availability matches the deployment's requirements.

SQLite is unsuitable for the reference deployment when you require multiple active Manager instances, cross-host distributed coordination, storage-level high availability, or database capabilities that the current adapter does not provide. Do not place the same SQLite file on a shared network filesystem and call that distributed safety.

### Backup

With the service running or stopped, use the SQLite backup API through:

```text
manager-deployment backup /var/lib/manager/state/manager.sqlite3 /backup/manager-YYYYMMDD.sqlite3
manager-deployment verify-backup /backup/manager-YYYYMMDD.sqlite3
```

The backup command writes a sibling manifest containing SHA-256, byte size, SQLite `user_version`, table list, and a schema fingerprint. The manifest provides corruption/change detection but is not a cryptographic authenticity signature.

Copy both database and manifest to durable backup storage. Backup retention and encryption are organization-specific.

### Restore drill

A backup is not accepted as operational evidence until it has been restored and verified.

1. stop all writers;
2. verify the selected backup;
3. restore into a new path first;
4. start a disposable process against the restored database and run state-level smoke tests;
5. only then replace a production database, using an explicit maintenance window and rollback plan.

Example:

```text
manager-deployment restore /backup/manager-YYYYMMDD.sqlite3 /restore-test/manager.sqlite3
```

Replacing an existing destination is refused unless `--replace` is explicit. Corrupt database files, checksum mismatches, malformed manifests, and schema mismatches fail closed.

## Upgrades

Use immutable application artifacts and persistent state kept outside the application image.

Recommended order:

1. build/verify the exact release candidate;
2. read its migration/checkpoint notes;
3. take and verify a state backup;
4. stop new work and drain the old version;
5. run any reviewed one-way migration step exactly once;
6. start the new version with readiness false;
7. validate configuration, state schema/checkpoint compatibility, and critical dependencies;
8. enable readiness only after smoke tests pass;
9. preserve the old artifact until rollback eligibility is known.

The current SQLite store auto-creates its existing table but does not provide a general schema migration framework. Unknown future checkpoint versions already fail closed. A future deployment migration must therefore be explicit and tested rather than inferred from application startup.

### Mixed-version operation

Do not assume mixed-version compatibility. The local SQLite reference deployment is single-instance, so rolling upgrades are not supported as a safety claim. For a future multi-instance backend, mixed-version compatibility must be established per release before rolling upgrades are allowed.

## Rollback

Application rollback is safe only when the old version can read the current persisted state and no irreversible migration or external semantic change has occurred.

If a release includes a one-way state migration, checkpoint format change, tool semantic change, or external side effect that the old version cannot understand, rollback may require restoring the pre-upgrade backup instead of simply launching the old binary. That is a data recovery operation and may discard post-upgrade local state, so the operator must reconcile consequential external effects first.

Never advertise one-click rollback without release-specific evidence.

## Resource guidance

The example Compose file uses conservative starting ceilings, not universal sizing guarantees:

- CPU: 2 cores;
- memory: 1 GiB;
- PIDs: 256;
- file descriptors: 4096;
- Manager concurrency: 8;
- queue: 64.

Measure real workloads and tune from evidence. Keep queueing bounded. Overload should reject or defer work rather than exhaust memory, file descriptors, provider quotas, or state connections.

## Disaster scenarios

| Scenario | Expected reference behavior |
| --- | --- |
| Container killed mid-run | Hard kill cannot drain. Durable `executing` state must not be blindly retried; reconcile uncertain effects. |
| Host restart | Persistent SQLite volume survives; validate config/state before becoming ready. |
| Database unavailable | Readiness false for state-dependent service paths; do not accept work that cannot be persisted safely. |
| Provider unavailable | Fail/decline provider-dependent work without changing Manager authority. |
| MCP unavailable | Fail affected calls closed; do not reinterpret transport failure as execution success. |
| Disk full | Writes/backups fail; readiness should be withdrawn if durable work cannot be recorded. |
| Read-only root | Supported when data/runtime paths are mounted writable and secrets are externally mounted. |
| Incorrect config | Startup validation exits nonzero before accepting work. |
| Missing secrets | Owning provider/MCP/service adapter must fail closed before required work is accepted. |
| Schema/checkpoint mismatch | Fail closed; use reviewed migration/recovery procedure. |
| Failed migration | Keep old service stopped, restore verified pre-migration backup when safe, and investigate. |
| Partial rollout | Not supported by the single-instance SQLite reference deployment. |
| Old/new overlap | Not supported unless a future backend/release explicitly proves mixed-version safety. |
| Backup restore | Restore to a separate path, integrity-check, then smoke-test before any production replacement. |

## External production decisions

The repository intentionally does not choose:

- cloud or container vendor;
- DNS/domain names;
- certificate issuer or enterprise TLS policy;
- production identity/credential mechanism;
- private network topology, proxy, or egress rules;
- backup retention/encryption provider;
- incident ownership and on-call policy;
- a distributed state backend.

Those decisions must be made by the deploying organization and bound to its own threat model.
