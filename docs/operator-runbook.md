# Manager operator runbook

This runbook assumes the operator has not read the source code. It covers the reference deployment boundary. The network-facing service command/endpoints must be taken from the service-runtime release being deployed.

## Before startup

1. Verify the exact application artifact/version and deployment manifest.
2. Verify production configuration with `manager-deployment validate-config`.
3. Confirm the persistent state volume is mounted at the configured data path.
4. Confirm required secret references are mounted/injected externally.
5. Confirm the termination grace period exceeds Manager's graceful-shutdown budget.
6. Confirm CPU, memory, PID, file-descriptor, concurrency, and queue limits are finite.
7. For upgrades, verify a fresh backup and restore drill before changing state.

Do not start production with the in-memory backend or more than one instance sharing the reference SQLite state.

## Startup

Start the service with readiness disabled. Validate configuration and state first. Enable readiness only after all critical dependencies required for safe work are usable.

A process that is live but not ready during startup is normal. A process that becomes ready before durable state is usable is an operational defect.

## Shutdown

Send SIGTERM. Expected sequence:

1. readiness becomes false;
2. no new work is accepted;
3. in-flight work drains up to the configured budget;
4. process exits;
5. the orchestrator may SIGKILL only after the grace period expires.

If SIGKILL or host loss occurs while a consequential action is in `executing`, treat the external result as uncertain and follow the runtime recovery/reconciliation procedure. Do not simply restart the action.

## Diagnostics

Check, in order:

1. deployment-config validation output;
2. liveness and readiness status;
3. available disk/inodes on the state and temporary volumes;
4. file-descriptor/PID/memory/CPU ceilings;
5. SQLite accessibility and integrity;
6. provider/MCP dependency status for the affected request path;
7. durable run state for `recovery_required` or incompatible checkpoints;
8. recent structured logs using correlation/run IDs without exposing secret payloads.

## Common failures

### Configuration exits immediately

Run `manager-deployment validate-config`. Unknown `MANAGER_DEPLOY_*` names, missing production-required settings, relative production paths, memory state, multiple SQLite instances, disabled production TLS, disabled telemetry, and inconsistent timeout/queue settings are rejected intentionally.

### Service is live but unready

Inspect readiness dependencies. During startup or graceful drain, unready is expected. For a persistent critical dependency failure, repair that dependency rather than bypassing readiness.

### SQLite cannot be written

Verify the configured SQLite parent and data directory are mounted writable for the non-root service UID. A read-only root filesystem is expected; only explicit runtime/state mounts should be writable.

### Disk full

Stop accepting new durable work. Free/expand the state filesystem, verify SQLite integrity, and only then restore readiness. Never delete durable run state blindly to create space.

### Provider or MCP outage

Fail affected work without widening authorization or turning uncertain execution into success. A provider/MCP outage is not permission to bypass policy or verification.

## Backup

```text
manager-deployment backup /path/to/manager.sqlite3 /backup/manager.sqlite3
manager-deployment verify-backup /backup/manager.sqlite3
```

Archive the database and its `.manifest.json` together. The manifest detects corruption/change but is not an authenticity signature.

## Restore

1. Stop all writers.
2. `manager-deployment verify-backup <backup>`.
3. Restore to a separate path.
4. Start a disposable/smoke-test instance against the restored copy.
5. Verify expected state and no schema/checkpoint errors.
6. Only then replace a production database if the recovery plan calls for it.

`--replace` is intentionally explicit and should be used only while writers are stopped.

## Upgrade

1. Verify the exact new artifact.
2. Review release-specific migration and checkpoint notes.
3. Backup and verify current state.
4. Drain and stop the old process.
5. Apply reviewed migration steps, if any.
6. Start the new process unready.
7. Verify state compatibility and smoke tests.
8. Enable readiness.

The single-instance SQLite reference path does not claim rolling-upgrade or mixed-version safety.

## Rollback

If the new version made no irreversible state/schema change and the old version can read current state, stop the new process and restart the verified old artifact.

If compatibility is uncertain or a one-way migration occurred, keep both versions stopped. Reconcile any consequential external effects first, then restore the verified pre-upgrade backup if the release-specific recovery plan permits it.

## Escalate instead of improvising when

- a run is `recovery_required` and external outcome is unknown;
- backup verification fails;
- SQLite integrity check fails;
- checkpoint/schema version is unsupported;
- secret/identity/TLS ownership is unclear;
- a rollout would require multiple active instances against SQLite;
- rollback would discard state created after consequential external actions.
