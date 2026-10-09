# Production service runtime

Manager includes a small vendor-neutral HTTP service implemented with the Python standard library. It exposes the existing deterministic governance/control-plane boundary without creating a second path around policy, approvals, durable execution, tool registration, or MCP controls.

## Endpoints

- `GET /livez` reports process liveness. It remains live while a graceful drain is in progress.
- `GET /readyz` and `GET /healthz` report whether the service is accepting work and whether critical dependencies are available. Operational saturation is included as a noncritical diagnostic so operators can see pressure without creating a readiness feedback loop.
- `POST /v1/run` accepts one strict JSON task envelope and invokes Manager's existing deterministic `engine.run()` control plane. This endpoint does **not** execute arbitrary tools, register MCP capabilities, resolve approvals, or bypass durable-action rules.

All other routes fail closed. `PUT`, `PATCH`, and `DELETE` are rejected.

## Authentication and API authorization

`staging` and `production` require an authenticated service mode. The reference service supports two application-owned modes:

- `MANAGER_SERVICE_AUTH_MODE=bearer` preserves the existing compatibility API-key boundary. The bearer value is read from deployment `secrets_dir` using `MANAGER_SERVICE_AUTH_SECRET_NAME` (default `service-auth-token`). Secret bytes are reacquired for each request so a mounted secret can rotate without a service restart.
- `MANAGER_SERVICE_AUTH_MODE=jwt_hs256` establishes an explicit API caller identity. It validates the token through Manager's strict reference JWT boundary and requires configured `MANAGER_SERVICE_AUTH_ISSUER` and `MANAGER_SERVICE_AUTH_AUDIENCE`. The service fixes the authenticated principal type to `api_client` by default, requires `nbf` and (by default) `jti`, validates signature/lifetime/issuer/audience, and separately requires the `manager.run` capability before `/v1/run` is accepted.

Authentication is not Manager authorization. A successfully authenticated and route-authorized API caller does not receive tool scope, approval, side-effect authority, or a trusted `task.authority` object. The service deliberately does not copy caller identity claims into model-visible task input. Consequential Manager actions still cross their normal application-owned authorization, approval, durable-state, and verification boundaries.

`MANAGER_SERVICE_AUTH_MODE=none` is allowed only for development/testing. Production startup rejects it. Multiple `Authorization` headers are rejected instead of relying on proxy/server first-header or last-header behavior.

JWT mode is an executable reference boundary, not a recommendation that every production deployment use shared-secret JWTs. Deployments using asymmetric OIDC/JWT, workload identity, SPIFFE/SVIDs, mTLS identities, or an external identity-aware ingress should preserve equivalent issuer, audience, lifetime, subject, capability, revocation, and network-trust checks. Static bearer mode remains a compatibility option and does not provide per-caller identity.

Health endpoints are intentionally unauthenticated so orchestrators can probe them. They expose only bounded health state and dependency names/details, never credentials or request payloads. SQLite readiness uses a short read-only metadata probe rather than the durable store's normal operation timeout, so lock pressure cannot consume a service worker for the store's full lock-wait budget.

## Request safety

The service rejects:

- missing, duplicate, signed, or otherwise noncanonical `Content-Length`;
- chunked/other transfer encodings on the reference endpoint;
- duplicate or invalid `Content-Type` framing;
- duplicate `Authorization` headers for authenticated service modes;
- non-`application/json` requests;
- bodies over `MANAGER_SERVICE_MAX_REQUEST_BYTES` (1 MiB by default, maximum 16 MiB);
- invalid UTF-8;
- duplicate JSON members;
- `NaN`/`Infinity` and other non-standard JSON constants;
- unknown top-level/task/classification fields;
- malformed or unbounded task identifiers, objectives, capabilities, or untrusted evidence.

Any rejection that can leave request-body bytes unread closes the HTTP/1.1 connection before another request can be parsed, preventing rejected-body bytes from being reinterpreted as a pipelined request.

The service uses bounded daemon request workers, bounded socket admission, and the shared `OperationalRuntime` for active-run capacity. Excess work is rejected instead of growing memory without bound. Run-level overload events, metrics, and health diagnostics therefore flow through the same sanitized observability boundary used by the rest of Manager. The optional `json_stdout` service sink receives telemetry only after that shared sanitization boundary.

## TLS

The deployment configuration supports:

- `tls_mode=external`: the recommended generic reference profile. Bind Manager behind an organization-owned TLS reverse proxy/ingress. The Compose example publishes the Manager port to host loopback only.
- `tls_mode=direct`: Manager wraps its listening socket with the configured certificate/key and enforces TLS 1.2 or newer.
- `tls_mode=off`: development/testing only. Staging/production reject it.

TLS configuration does not replace authentication or API authorization. Production trust roots, certificate lifecycle, client-certificate policy, ingress identity propagation, and enterprise proxy/egress controls remain deployment responsibilities.

## Shutdown

`SIGTERM`/`SIGINT` immediately make readiness false, keep liveness true during drain, stop accepting new connections, and wait only up to `MANAGER_DEPLOY_GRACEFUL_SHUTDOWN_SECONDS` for accepted HTTP work to drain. Request workers are daemon threads specifically so an uncooperative handler cannot silently extend the documented process-exit budget through Python executor shutdown behavior. Container `stop_grace_period` should exceed the configured graceful-shutdown budget and the deployment request ceiling should remain finite.

## Production constraints

The reference SQLite backend is single-instance only. Production configuration rejects `instance_count > 1` with SQLite. Horizontal scaling requires a production coordinated `RunStore` implementation that preserves Manager's CAS, lease, fencing, operation-ledger, and recovery semantics transactionally.

The service boundary is production-capable code, not proof that an arbitrary deployment is production-ready. A real deployment still needs organization-specific identity-provider/key distribution, revocation policy, TLS/DNS, secret management, network/egress policy, telemetry backend, backup retention/encryption, SLOs/on-call ownership, and deployment-specific load/failure evidence.
