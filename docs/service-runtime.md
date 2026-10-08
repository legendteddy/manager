# Production service runtime

Manager includes a small vendor-neutral HTTP service implemented with the Python standard library. It exposes the existing deterministic governance/control-plane boundary without creating a second path around policy, approvals, durable execution, tool registration, or MCP controls.

## Endpoints

- `GET /livez` reports process liveness. It remains live while a graceful drain is in progress.
- `GET /readyz` and `GET /healthz` report whether the service is accepting work and whether critical dependencies are available.
- `POST /v1/run` accepts one strict JSON task envelope and invokes Manager's existing deterministic `engine.run()` control plane. This endpoint does **not** execute arbitrary tools, register MCP capabilities, resolve approvals, or bypass durable-action rules.

All other routes fail closed. `PUT`, `PATCH`, and `DELETE` are rejected.

## Authentication

`staging` and `production` require bearer authentication. The reference implementation reads the bearer value from the deployment `secrets_dir` using the secret name in `MANAGER_SERVICE_AUTH_SECRET_NAME` (default `service-auth-token`). Secret bytes are loaded on each authenticated request, so a mounted secret can be rotated without restarting the service. The token is never emitted in responses or telemetry.

`MANAGER_SERVICE_AUTH_MODE=none` is allowed only for development/testing. Production startup rejects it.

Health endpoints are intentionally unauthenticated so orchestrators can probe them. They expose only bounded health state and dependency names/details, never credentials or request payloads.

For deployments that require user/workload identity and fine-grained capabilities, use the repository's `security.py` identity and authorization primitives at the embedding ingress/proxy boundary in addition to the service bearer credential. Do not treat a reverse proxy header as identity unless the proxy/network trust policy is explicitly configured.

## Request safety

The service rejects:

- missing or invalid `Content-Length`;
- chunked/other transfer encodings on the reference endpoint;
- non-`application/json` requests;
- bodies over `MANAGER_SERVICE_MAX_REQUEST_BYTES` (1 MiB by default, maximum 16 MiB);
- invalid UTF-8;
- duplicate JSON members;
- `NaN`/`Infinity` and other non-standard JSON constants;
- unknown top-level/task/classification fields;
- malformed or unbounded task identifiers, objectives, capabilities, or untrusted evidence.

The service uses a fixed worker pool, bounded admission queue, request socket timeout, and a separate active-run capacity gate. Excess work is rejected instead of growing memory without bound.

## TLS

The deployment configuration supports:

- `tls_mode=external`: the recommended generic reference profile. Bind Manager behind an organization-owned TLS reverse proxy/ingress. The Compose example publishes the Manager port to host loopback only.
- `tls_mode=direct`: Manager wraps its listening socket with the configured certificate/key.
- `tls_mode=off`: development/testing only. Staging/production reject it.

TLS configuration does not replace authentication.

## Shutdown

`SIGTERM`/`SIGINT` immediately make readiness false, keep liveness true during drain, stop accepting new connections, and shut down the bounded HTTP executor. Container `stop_grace_period` should exceed `MANAGER_DEPLOY_GRACEFUL_SHUTDOWN_SECONDS` and the deployment request ceiling should remain finite.

## Production constraints

The reference SQLite backend is single-instance only. Production configuration rejects `instance_count > 1` with SQLite. Horizontal scaling requires a production coordinated `RunStore` implementation that preserves Manager's CAS, lease, fencing, operation-ledger, and recovery semantics transactionally.

The service boundary is production-capable code, not proof that an arbitrary deployment is production-ready. A real deployment still needs organization-specific identity, TLS/DNS, secret management, network/egress policy, telemetry backend, backup retention/encryption, SLOs/on-call ownership, and deployment-specific load/failure evidence.
