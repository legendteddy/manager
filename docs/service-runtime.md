# Production service runtime

Manager now has two Python HTTP surfaces with deliberately different roles:

- `manager_runtime.service.server` remains the small deterministic/reference control-plane server used by compatibility tests and examples.
- `manager_runtime.service.gateway` is the production network boundary used by the `manager-service` console command. It exposes durable run lifecycle operations without allowing HTTP clients to supply Manager authority, trusted tool metadata, provider credentials, authorization context, or MCP connection policy.

The production gateway composes the existing policy, durable-state, approval, recovery, provider, tool, and MCP boundaries. It is not a shortcut around them.

## Production startup

`manager-service` loads normal `MANAGER_DEPLOY_*` settings plus `MANAGER_SERVICE_*` settings. In staging and production startup fails before accepting work unless bearer authentication is enabled, its mounted secret is readable, a backend factory is configured and imports successfully, deployment/TLS/runtime-path validation succeeds, and the durable API idempotency database is usable.

The application-owned backend factory is configured as:

```text
MANAGER_SERVICE_BACKEND_FACTORY=module.path:callable
```

The callable receives `(DeploymentConfig, GatewaySettings)` and must return the service backend. The factory owns construction of the approved model adapter, `ToolRegistry`, durable `RunStore`, MCP bindings, and authorization resolver. These trusted values are never accepted from client request bodies.

`DurableRuntimeBackend` is the reference bridge for applications that already hold those trusted objects. It delegates execution to `run_durable_agent_loop`, `resume_durable_agent_loop`, and `resolve_recovery_required` rather than reimplementing their governance rules.

Important service settings include:

- `MANAGER_SERVICE_AUTH_MODE` (`bearer` is mandatory in staging/production);
- `MANAGER_SERVICE_AUTH_SECRET_NAME`;
- `MANAGER_SERVICE_BACKEND_FACTORY`;
- `MANAGER_SERVICE_IDEMPOTENCY_PATH`;
- `MANAGER_SERVICE_MAX_REQUEST_BYTES`;
- `MANAGER_SERVICE_MAX_HEADER_BYTES`;
- `MANAGER_SERVICE_MAX_JSON_DEPTH`;
- `MANAGER_SERVICE_MAX_JSON_NODES`;
- `MANAGER_SERVICE_MAX_STRING_CHARS`.

Unknown `MANAGER_SERVICE_*` settings fail closed.

## API contract

Health probes are unauthenticated and expose only bounded health state. All `/v1/*` endpoints use the configured service authentication mode.

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/livez` | Process liveness. Remains live while graceful drain is in progress. |
| `GET` | `/readyz` | Whether the service can safely accept new work. |
| `GET` | `/healthz` | Readiness alias for deployment integrations. |
| `GET` | `/v1/version` | API/runtime version metadata. |
| `GET` | `/v1/capabilities` | Explicit supported feature flags. |
| `POST` | `/v1/runs` | Submit one governed durable task. |
| `GET` | `/v1/runs/{run_id}` | Query the bounded public run view. |
| `POST` | `/v1/runs/{run_id}/resume` | Resume without inventing an approval decision. |
| `POST` | `/v1/runs/{run_id}/cancel` | Cancel only when the durable state graph says cancellation is safe. |
| `POST` | `/v1/runs/{run_id}/approval` | Submit an exact `approved` or `rejected` approval decision. |
| `POST` | `/v1/runs/{run_id}/recovery` | Resolve `recovery_required` from explicit external evidence. |

Every mutating endpoint requires exactly one canonical `Idempotency-Key` header.

Success uses a small envelope:

```json
{"ok":true,"request_id":"request:...","data":{}}
```

Errors are structured and safe:

```json
{
  "ok": false,
  "request_id": "request:...",
  "error": {
    "code": "...",
    "message": "...",
    "retryable": false,
    "recovery_required": false
  }
}
```

The service never returns raw stack traces, authorization headers, provider exceptions, MCP errors, connection configuration, or durable checkpoint internals.

## Public run view

Run queries do not serialize the durable store row. They project only run/task identity, status, revision, timestamps, the public result snapshot when present, a minimal pending approval identity/status, and whether recovery is required.

Raw pending tool arguments, authorization context, tool-definition fingerprints, provider continuation state, MCP provenance/configuration, leases, fencing tokens, and operation-ledger internals remain private to the runtime.

## Request validation

The gateway rejects malformed clients deterministically. The boundary enforces:

- bounded request body and aggregate header bytes;
- exactly one canonical decimal `Content-Length` and no `Transfer-Encoding`;
- no compressed/request content encoding other than absent/identity;
- `application/json` with UTF-8 only;
- strict UTF-8 JSON, duplicate-member rejection, and rejection of `NaN`/`Infinity`;
- object/field allowlists and required fields for API commands;
- bounded JSON nesting, node count, field names, and strings;
- reserved prototype-style key rejection;
- canonical task/run/idempotency identifiers;
- bounded task classification and capability shapes;
- no query-string variants of lifecycle endpoints;
- rejection of `Expect: 100-continue` on this reference protocol.

Parser-level HTTP errors are converted to JSON-safe error envelopes. If a rejection may leave request-body bytes unread, the HTTP/1.1 connection is closed before it can be reused so rejected bytes cannot be interpreted as a pipelined request.

## Durable network idempotency

Core Manager already has durable run identity, revision CAS, leases, fencing, and an external-operation ledger. The gateway adds a separate SQLite network-request idempotency ledger so retries cannot create a second service submission around those controls.

A record binds the authenticated service subject, idempotency key, method, path, canonical payload fingerprint, and run identity where known:

- the first request writes `in_progress` before backend execution;
- the same request while active receives `202 in_progress` and does not start a second operation;
- a completed request replays its cached safe response;
- reusing a key for different request identity returns `409 idempotency_conflict`;
- a process restart converts orphaned `in_progress` requests to `ambiguous`, never to automatic retry;
- an unexpected backend failure after ownership begins also becomes ambiguous and a duplicate is refused until the run is queried/reconciled.

A backend may explicitly raise a retryable `ApiError` only when it knows no consequential effect occurred. In that narrower case the network claim can be released for safe retry.

This is not a claim of exactly-once provider calls or exactly-once external effects. The existing `executing`/`recovery_required` and recovery-evidence rules remain authoritative.

## Request timeout and disconnect semantics

`request_timeout_seconds` bounds how long the HTTP request waits for a newly accepted mutation. Once the idempotency claim has been durably accepted, work is owned by the service rather than by the TCP connection.

If the wait ceiling expires, the client receives `202 in_progress` while the bounded daemon operation can continue and persist its outcome. A client disconnect during response delivery likewise does not cancel an accepted operation. Neither condition means "cancel an approved side effect".

Provider, tool, state-backend, and MCP operation timeouts remain owned by their respective runtime/adapters. The backend factory must apply the deployment provider/MCP timeout settings when constructing those dependencies.

## Cancellation

Cancellation is intentionally conservative:

- `waiting_approval` may become `cancelled` before the side effect starts;
- `running` and `executing` are not forcibly cancelled by the HTTP layer;
- `recovery_required` must be resolved through the recovery endpoint, where `cancelled` is an explicit recovery decision backed by evidence;
- terminal runs are returned as terminal and never resurrected.

A dropped connection never changes these rules.

## Health and readiness

Liveness means the process can still function. Readiness means the service can safely accept new work.

Readiness becomes false when a critical service credential, backend readiness check, or network-idempotency database is unavailable, and immediately when graceful drain starts. Capacity saturation is reported diagnostically rather than treated as process death. `DurableRuntimeBackend` also requires the durable store capabilities needed for coordinated consequential execution.

Provider/MCP availability is not universally a liveness requirement. A deployment-specific backend may make a dependency readiness-critical when safe work genuinely depends on it.

## Graceful shutdown

`SIGTERM`/`SIGINT` first withdraw readiness and stop new acceptance. The gateway drains accepted API mutations and HTTP workers only within `graceful_shutdown_seconds`.

If an accepted mutation is still unfinished after the drain budget, its network idempotency record becomes `ambiguous`. On the next process start, any other orphaned `in_progress` records are also made ambiguous before readiness. Manager does not use a daemon thread's disappearance as evidence that an external side effect did or did not occur.

## Authentication scope

The reference bearer mode represents one stable service principal. Its identity is derived from the configured secret name rather than token bytes, so secret rotation does not orphan service-owned runs. Secret bytes are loaded for authentication and are not stored in the API ledgers or responses.

This is not a multi-user identity/RBAC system. Deployments needing per-user or workload authorization must supply a reviewed identity boundary and construct Manager authorization server-side. Arbitrary forwarded identity headers are not trusted by this gateway.

## MCP and streaming decisions

The production capability endpoint intentionally reports:

- `raw_mcp_gateway: false`;
- `streaming: false`.

MCP remains an internal application-owned interoperability adapter behind `ToolRegistry`, policy, approval, verification, and recovery. Exposing Manager itself as a raw MCP server would establish a new public protocol and authority surface and requires separate design/review.

Likewise, this worker does not claim SSE/stream conformance. A future streaming contract must define durable event identity, reconnect/resume behavior, framing/error rules, backpressure, stream limits, shutdown semantics, and authorization before exposure.

## Deployment assumptions and residual risks

The gateway is production-capable boundary code, not proof that every deployment is production-ready. The deploying organization still owns production identity/RBAC, TLS/DNS, secret management, provider/MCP credentials and OAuth, private network/egress policy, telemetry storage, backup/retention/encryption, load/SLO evidence, and incident/on-call ownership.

The reference SQLite path is single-instance. Multi-instance service operation requires a distributed durable state/idempotency implementation with equivalent transactional CAS, leases/fencing, durable idempotency, execution guards, and recovery semantics.

No production deployment is performed by this repository worker.
