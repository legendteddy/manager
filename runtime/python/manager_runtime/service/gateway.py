from __future__ import annotations

import hashlib
import hmac
import importlib
import importlib.metadata
import json
import os
import re
import socket
import ssl
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from ..capacity import CapacityLimits
from ..deployment import DependencyCheck, GracefulShutdown, HealthRegistry, validate_runtime_paths
from ..deployment.config import DeploymentConfig, load_deployment_config
from ..operations import OperationalRuntime
from ..security import MountedFileSecretProvider, SecurityBoundaryError
from .api import (
    ApiError,
    JsonLimits,
    ServiceBackend,
    canonical_id,
    canonical_idempotency_key,
    validate_approval_decision,
    validate_empty_command,
    validate_json_limits,
    validate_recovery_resolution,
    validate_task_input,
)
from .idempotency import IdempotencyError, SQLiteIdempotencyStore, canonical_fingerprint
from .worker_pool import BoundedDaemonWorkerPool

_SERVICE_PREFIX = "MANAGER_SERVICE_"
_ALLOWED_SERVICE_ENV = {
    "MANAGER_SERVICE_AUTH_MODE",
    "MANAGER_SERVICE_AUTH_SECRET_NAME",
    "MANAGER_SERVICE_BACKEND_FACTORY",
    "MANAGER_SERVICE_IDEMPOTENCY_PATH",
    "MANAGER_SERVICE_MAX_REQUEST_BYTES",
    "MANAGER_SERVICE_MAX_HEADER_BYTES",
    "MANAGER_SERVICE_MAX_JSON_DEPTH",
    "MANAGER_SERVICE_MAX_JSON_NODES",
    "MANAGER_SERVICE_MAX_STRING_CHARS",
}
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:/-]{1,128}$")
_FACTORY = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*:[A-Za-z_][A-Za-z0-9_]*$")


class GatewayConfigError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class GatewaySettings:
    auth_mode: str
    auth_secret_name: str
    backend_factory: str | None
    idempotency_path: str
    max_request_bytes: int
    max_header_bytes: int
    json_limits: JsonLimits


def _bounded_int(raw: Any, *, field: str, minimum: int, maximum: int) -> int:
    if isinstance(raw, bool):
        raise GatewayConfigError(f"{field} must be an integer")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise GatewayConfigError(f"{field} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise GatewayConfigError(f"{field} must be between {minimum} and {maximum}")
    return value


def load_gateway_settings(
    config: DeploymentConfig,
    *,
    environ: Mapping[str, str] | None = None,
) -> GatewaySettings:
    env = os.environ if environ is None else environ
    unknown = sorted(
        key for key in env if key.startswith(_SERVICE_PREFIX) and key not in _ALLOWED_SERVICE_ENV
    )
    if unknown:
        raise GatewayConfigError(f"unknown service environment settings: {', '.join(unknown)}")

    default_auth = "bearer" if config.environment in {"staging", "production"} else "none"
    auth_mode = env.get("MANAGER_SERVICE_AUTH_MODE", default_auth).strip().lower()
    if auth_mode not in {"none", "bearer"}:
        raise GatewayConfigError("MANAGER_SERVICE_AUTH_MODE must be none or bearer")
    if config.environment in {"staging", "production"} and auth_mode != "bearer":
        raise GatewayConfigError(f"{config.environment} service authentication must use bearer mode")

    auth_secret_name = env.get("MANAGER_SERVICE_AUTH_SECRET_NAME", "service-auth-token").strip()
    if not auth_secret_name or re.fullmatch(r"[A-Za-z0-9_.-]+", auth_secret_name) is None:
        raise GatewayConfigError("MANAGER_SERVICE_AUTH_SECRET_NAME is invalid")
    if auth_mode == "bearer" and not config.secrets_dir:
        raise GatewayConfigError("bearer service authentication requires deployment secrets_dir")

    backend_factory = env.get("MANAGER_SERVICE_BACKEND_FACTORY")
    if backend_factory is not None:
        backend_factory = backend_factory.strip()
        if not backend_factory or _FACTORY.fullmatch(backend_factory) is None:
            raise GatewayConfigError("MANAGER_SERVICE_BACKEND_FACTORY must be module.path:callable")
    if config.environment in {"staging", "production"} and backend_factory is None:
        raise GatewayConfigError(f"{config.environment} requires MANAGER_SERVICE_BACKEND_FACTORY")

    idempotency_path = env.get("MANAGER_SERVICE_IDEMPOTENCY_PATH")
    if not idempotency_path:
        idempotency_path = config.sqlite_path or str(
            Path(config.data_dir) / "manager-api-idempotency.sqlite3"
        )
    if "\x00" in idempotency_path:
        raise GatewayConfigError("MANAGER_SERVICE_IDEMPOTENCY_PATH is invalid")
    if config.environment in {"staging", "production"} and not Path(idempotency_path).is_absolute():
        raise GatewayConfigError("production idempotency path must be absolute")

    return GatewaySettings(
        auth_mode=auth_mode,
        auth_secret_name=auth_secret_name,
        backend_factory=backend_factory,
        idempotency_path=idempotency_path,
        max_request_bytes=_bounded_int(
            env.get("MANAGER_SERVICE_MAX_REQUEST_BYTES", 1024 * 1024),
            field="MANAGER_SERVICE_MAX_REQUEST_BYTES",
            minimum=1024,
            maximum=16 * 1024 * 1024,
        ),
        max_header_bytes=_bounded_int(
            env.get("MANAGER_SERVICE_MAX_HEADER_BYTES", 32 * 1024),
            field="MANAGER_SERVICE_MAX_HEADER_BYTES",
            minimum=1024,
            maximum=256 * 1024,
        ),
        json_limits=JsonLimits(
            max_depth=_bounded_int(
                env.get("MANAGER_SERVICE_MAX_JSON_DEPTH", 32),
                field="MANAGER_SERVICE_MAX_JSON_DEPTH",
                minimum=4,
                maximum=128,
            ),
            max_nodes=_bounded_int(
                env.get("MANAGER_SERVICE_MAX_JSON_NODES", 10_000),
                field="MANAGER_SERVICE_MAX_JSON_NODES",
                minimum=128,
                maximum=1_000_000,
            ),
            max_string_chars=_bounded_int(
                env.get("MANAGER_SERVICE_MAX_STRING_CHARS", 65_536),
                field="MANAGER_SERVICE_MAX_STRING_CHARS",
                minimum=256,
                maximum=1_000_000,
            ),
        ),
    )


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is not permitted: {value}")


def _decode_json(raw: bytes) -> Any:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ApiError("invalid_request", "request body must be UTF-8") from exc
    try:
        return json.loads(text, object_pairs_hook=_strict_pairs, parse_constant=_reject_constant)
    except (json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise ApiError("invalid_request", "request body is not strict JSON") from exc


class _Authenticator:
    def __init__(self, config: DeploymentConfig, settings: GatewaySettings) -> None:
        self.mode = settings.auth_mode
        self.secret_name = settings.auth_secret_name
        self._provider = (
            MountedFileSecretProvider(config.secrets_dir)
            if self.mode == "bearer" and config.secrets_dir is not None
            else None
        )
        self.subject = (
            f"service:{settings.auth_secret_name}" if self.mode == "bearer" else "anonymous"
        )

    def ready(self) -> bool | tuple[bool, str]:
        if self.mode == "none":
            return True
        try:
            assert self._provider is not None
            secret = self._provider.acquire(self.secret_name).reveal()
            if not secret:
                return False, "service credential unavailable"
        except Exception:
            return False, "service credential unavailable"
        return True

    def require_available(self) -> None:
        result = self.ready()
        ok = result if type(result) is bool else result[0]
        if not ok:
            raise GatewayConfigError("service authentication credential is unavailable")

    def authenticate(self, authorization: str | None) -> str | None:
        if self.mode == "none":
            return self.subject
        if not isinstance(authorization, str) or len(authorization) > 16_384:
            return None
        if not authorization.startswith("Bearer "):
            return None
        supplied = authorization[7:].encode("utf-8", errors="strict")
        if not supplied:
            return None
        try:
            assert self._provider is not None
            expected = self._provider.acquire(self.secret_name).reveal()
        except (SecurityBoundaryError, OSError, ValueError):
            return None
        if not hmac.compare_digest(supplied, expected):
            return None
        return self.subject


class _JsonStdoutTelemetrySink:
    def __init__(self) -> None:
        self._lock = threading.Lock()

    def _emit(self, payload: dict[str, Any]) -> None:
        line = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        with self._lock:
            print(line, file=sys.stdout, flush=True)

    def emit_event(self, event: Any) -> None:
        self._emit({"event": event.name, **dict(event.attributes)})

    def emit_metric(self, metric: Any) -> None:
        self._emit(
            {
                "metric": metric.name,
                "kind": metric.kind,
                "value": metric.value,
                "labels": dict(metric.labels),
            }
        )

    def emit_span(self, span: Any) -> None:
        self._emit(
            {
                "span": span.name,
                "duration_seconds": span.duration_seconds,
                "status": span.status,
                "labels": dict(span.labels),
            }
        )


def _service_operations(config: DeploymentConfig) -> OperationalRuntime:
    sink = _JsonStdoutTelemetrySink() if config.telemetry_mode == "json_stdout" else None
    return OperationalRuntime(
        limits=CapacityLimits(
            active_runs=config.max_concurrency,
            queued_runs=config.queue_limit,
        ),
        telemetry_sink=sink,
    )


def _operations_dependency(operations: OperationalRuntime) -> tuple[bool, str]:
    status = str(operations.health().get("status") or "degraded")
    return status == "ok", f"status={status}"


def _validate_backend(backend: Any) -> ServiceBackend:
    required = (
        "submit",
        "get_run",
        "resume",
        "cancel",
        "decide_approval",
        "resolve_recovery",
        "readiness",
    )
    missing = [name for name in required if not callable(getattr(backend, name, None))]
    if missing:
        raise GatewayConfigError(f"service backend is missing methods: {', '.join(missing)}")
    return backend


def load_backend_factory(
    factory_path: str,
    config: DeploymentConfig,
    settings: GatewaySettings,
) -> ServiceBackend:
    module_name, callable_name = factory_path.split(":", 1)
    try:
        module = importlib.import_module(module_name)
        factory = getattr(module, callable_name)
    except (ImportError, AttributeError) as exc:
        raise GatewayConfigError("service backend factory could not be imported") from exc
    if not callable(factory):
        raise GatewayConfigError("service backend factory is not callable")
    try:
        backend = factory(config, settings)
    except Exception as exc:
        raise GatewayConfigError(
            f"service backend factory failed with {type(exc).__name__}"
        ) from exc
    return _validate_backend(backend)


@dataclass(slots=True)
class _OperationResult:
    status: int
    payload: dict[str, Any]


@dataclass(slots=True)
class _ActiveOperation:
    subject: str
    key: str
    run_id: str | None
    event: threading.Event
    result: _OperationResult | None = None


class _OperationCoordinator:
    def __init__(
        self,
        *,
        max_concurrency: int,
        wait_timeout_seconds: float,
        shutdown_timeout_seconds: float,
        idempotency: SQLiteIdempotencyStore,
    ) -> None:
        self._slots = threading.BoundedSemaphore(max_concurrency)
        self._wait_timeout_seconds = wait_timeout_seconds
        self._shutdown_timeout_seconds = shutdown_timeout_seconds
        self._idempotency = idempotency
        self._lock = threading.Condition()
        self._active: dict[tuple[str, str], _ActiveOperation] = {}
        self._closing = False

    def execute(
        self,
        *,
        subject: str,
        key: str,
        run_id: str | None,
        request_id: str,
        function: Callable[[], dict[str, Any]],
    ) -> _OperationResult:
        if not self._slots.acquire(blocking=False):
            self._idempotency.abandon(subject=subject, key=key)
            raise ApiError(
                "service_overloaded",
                "service capacity is exhausted",
                status=503,
                retryable=True,
            )
        operation = _ActiveOperation(subject, key, run_id, threading.Event())
        identity = (subject, key)
        with self._lock:
            if self._closing:
                self._slots.release()
                self._idempotency.abandon(subject=subject, key=key)
                raise ApiError(
                    "service_draining",
                    "service is draining",
                    status=503,
                    retryable=True,
                )
            self._active[identity] = operation

        def worker() -> None:
            try:
                try:
                    data = function()
                    payload = {"ok": True, "request_id": request_id, "data": data}
                    result = _OperationResult(200, payload)
                    completed = self._idempotency.complete(
                        subject=subject,
                        key=key,
                        status_code=result.status,
                        response=result.payload,
                    )
                    if not completed:
                        result = _OperationResult(
                            409,
                            ApiError(
                                "operation_outcome_unknown",
                                "operation completed after its network ownership became ambiguous",
                                status=409,
                                recovery_required=True,
                                ambiguous=True,
                            ).envelope(request_id, run_id=run_id),
                        )
                except ApiError as exc:
                    if exc.ambiguous:
                        self._idempotency.mark_ambiguous(subject=subject, key=key)
                    elif exc.retryable:
                        self._idempotency.abandon(subject=subject, key=key)
                    else:
                        payload = exc.envelope(request_id, run_id=run_id)
                        self._idempotency.complete(
                            subject=subject,
                            key=key,
                            status_code=exc.status,
                            response=payload,
                        )
                    result = _OperationResult(
                        exc.status,
                        exc.envelope(request_id, run_id=run_id),
                    )
                except BaseException:
                    self._idempotency.mark_ambiguous(subject=subject, key=key)
                    error = ApiError(
                        "operation_outcome_unknown",
                        "operation outcome is unknown; query the run before retrying",
                        status=500,
                        recovery_required=True,
                        ambiguous=True,
                    )
                    result = _OperationResult(
                        500,
                        error.envelope(request_id, run_id=run_id),
                    )
                operation.result = result
            finally:
                operation.event.set()
                with self._lock:
                    self._active.pop(identity, None)
                    self._lock.notify_all()
                self._slots.release()

        threading.Thread(
            target=worker,
            name=f"manager-api-{hashlib.sha256(key.encode()).hexdigest()[:12]}",
            daemon=True,
        ).start()
        if operation.event.wait(self._wait_timeout_seconds):
            assert operation.result is not None
            return operation.result
        return _OperationResult(
            202,
            {
                "ok": True,
                "request_id": request_id,
                "data": {
                    "status": "in_progress",
                    "run_id": run_id,
                    "poll": f"/v1/runs/{run_id}" if run_id else None,
                },
            },
        )

    def shutdown(self) -> bool:
        with self._lock:
            self._closing = True
            deadline = time.monotonic() + self._shutdown_timeout_seconds
            while self._active:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._lock.wait(remaining)
            active = tuple(self._active.values())
        for operation in active:
            self._idempotency.mark_ambiguous(
                subject=operation.subject,
                key=operation.key,
            )
        return not active


@dataclass(slots=True)
class GatewayContext:
    config: DeploymentConfig
    settings: GatewaySettings
    backend: ServiceBackend
    idempotency: SQLiteIdempotencyStore
    health: HealthRegistry
    authenticator: _Authenticator
    operations: OperationalRuntime
    coordinator: _OperationCoordinator


def _package_version() -> str:
    try:
        return importlib.metadata.version("manager-reference-runtime")
    except importlib.metadata.PackageNotFoundError:
        return "0.10.0"


class _GatewayHTTPServer(HTTPServer):
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        *,
        context: GatewayContext,
    ) -> None:
        self.context = context
        workers = context.config.max_concurrency + 4
        pending = workers + max(context.config.queue_limit, 0)
        self.request_queue_size = max(5, min(pending, 1024))
        self._slots = threading.BoundedSemaphore(pending)
        self._pool = BoundedDaemonWorkerPool(
            max_workers=workers,
            max_pending=pending,
            shutdown_timeout_seconds=context.config.graceful_shutdown_seconds,
            thread_name_prefix="manager-gateway-http",
        )
        super().__init__(server_address, handler, bind_and_activate=True)

    def process_request(self, request: socket.socket, client_address: tuple[str, int]) -> None:
        if not self._slots.acquire(blocking=False):
            try:
                body = b'{"error":"service_overloaded","ok":false}'
                request.sendall(
                    b"HTTP/1.1 503 Service Unavailable\r\n"
                    b"Content-Type: application/json\r\n"
                    + f"Content-Length: {len(body)}\r\n".encode()
                    + b"Connection: close\r\n\r\n"
                    + body
                )
            except OSError:
                pass
            finally:
                self.shutdown_request(request)
            return
        try:
            self._pool.submit(self._worker, request, client_address)
        except Exception:
            self._slots.release()
            self.shutdown_request(request)
            raise

    def _worker(self, request: socket.socket, client_address: tuple[str, int]) -> None:
        try:
            self.finish_request(request, client_address)
        except Exception:
            pass
        finally:
            try:
                self.shutdown_request(request)
            finally:
                self._slots.release()

    def handle_error(self, request: Any, client_address: Any) -> None:
        return

    def server_close(self) -> None:
        try:
            self.context.coordinator.shutdown()
            super().server_close()
        finally:
            self._pool.shutdown(wait=True)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "manager"
    sys_version = ""

    @property
    def context(self) -> GatewayContext:
        return self.server.context  # type: ignore[attr-defined]

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(self.context.config.request_timeout_seconds)

    def version_string(self) -> str:
        return "manager"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _request_id(self) -> str:
        values = self.headers.get_all("X-Request-ID") or []
        if len(values) == 1 and _REQUEST_ID.fullmatch(values[0]):
            return values[0]
        return f"request:{uuid.uuid4().hex}"

    def send_error(
        self,
        code: int,
        message: str | None = None,
        explain: str | None = None,
    ) -> None:
        del message, explain
        self.close_connection = True
        request_id = f"request:{uuid.uuid4().hex}"
        try:
            headers = getattr(self, "headers", None)
            if headers is not None:
                values = headers.get_all("X-Request-ID") or []
                if len(values) == 1 and _REQUEST_ID.fullmatch(values[0]):
                    request_id = values[0]
            self._reply(
                code,
                ApiError(
                    "invalid_http_request",
                    "HTTP request was rejected",
                    status=code,
                ).envelope(request_id),
                request_id=request_id,
            )
        except Exception:
            pass

    def handle_expect_100(self) -> bool:
        self.close_connection = True
        request_id = self._request_id()
        self._reply(
            417,
            ApiError(
                "expectation_failed",
                "Expect: 100-continue is not supported",
                status=417,
            ).envelope(request_id),
            request_id=request_id,
        )
        return False

    def _reply(
        self,
        status: int,
        payload: dict[str, Any],
        *,
        request_id: str,
        extra_headers: Mapping[str, str] | None = None,
    ) -> None:
        raw = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Request-ID", request_id)
        if self.close_connection:
            self.send_header("Connection", "close")
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.end_headers()
        if getattr(self, "command", None) != "HEAD":
            try:
                self.wfile.write(raw)
                self.wfile.flush()
            except OSError:
                pass

    def _error(
        self,
        error: ApiError,
        *,
        request_id: str,
        run_id: str | None = None,
    ) -> None:
        self._reply(
            error.status,
            error.envelope(request_id, run_id=run_id),
            request_id=request_id,
        )

    def _header_bounds(self) -> None:
        total = 0
        for key, value in self.headers.items():
            total += len(key.encode("utf-8", errors="ignore"))
            total += len(value.encode("utf-8", errors="ignore"))
            total += 4
            if total > self.context.settings.max_header_bytes:
                raise ApiError(
                    "request_headers_too_large",
                    "request headers exceed safe limits",
                    status=431,
                )

    def _subject(self) -> str:
        subject = self.context.authenticator.authenticate(self.headers.get("Authorization"))
        if subject is None:
            raise ApiError("unauthorized", "authentication is required", status=401)
        return subject

    def _path(self) -> str:
        parsed = urlsplit(self.path)
        if parsed.query or parsed.fragment:
            raise ApiError(
                "invalid_request",
                "query strings are not supported on this API",
            )
        return parsed.path

    def _read_json(self) -> Any:
        if self.headers.get_all("Transfer-Encoding"):
            self.close_connection = True
            raise ApiError(
                "unsupported_transfer_encoding",
                "Transfer-Encoding is not supported",
                status=400,
            )
        encodings = self.headers.get_all("Content-Encoding") or []
        if len(encodings) > 1 or (
            encodings and encodings[0].strip().lower() not in {"", "identity"}
        ):
            self.close_connection = True
            raise ApiError(
                "unsupported_content_encoding",
                "Content-Encoding is not supported",
                status=415,
            )
        content_types = self.headers.get_all("Content-Type") or []
        if len(content_types) != 1:
            self.close_connection = True
            raise ApiError(
                "unsupported_content_type",
                "Content-Type must be application/json",
                status=415,
            )
        parts = [part.strip() for part in content_types[0].split(";")]
        if not parts or parts[0].lower() != "application/json":
            self.close_connection = True
            raise ApiError(
                "unsupported_content_type",
                "Content-Type must be application/json",
                status=415,
            )
        for parameter in parts[1:]:
            if not parameter:
                continue
            name, separator, value = parameter.partition("=")
            if (
                separator != "="
                or name.strip().lower() != "charset"
                or value.strip().strip(chr(34)).lower() not in {"utf-8", "utf8"}
            ):
                self.close_connection = True
                raise ApiError(
                    "unsupported_content_type",
                    "only UTF-8 application/json is supported",
                    status=415,
                )
        lengths = self.headers.get_all("Content-Length") or []
        if not lengths:
            self.close_connection = True
            raise ApiError(
                "content_length_required",
                "Content-Length is required",
                status=411,
            )
        raw_length = lengths[0]
        if len(lengths) != 1 or re.fullmatch(r"[0-9]+", raw_length) is None:
            self.close_connection = True
            raise ApiError(
                "invalid_content_length",
                "Content-Length is invalid",
                status=400,
            )
        if len(raw_length) > 20:
            self.close_connection = True
            raise ApiError(
                "request_too_large",
                "request body exceeds safe limits",
                status=413,
            )
        length = int(raw_length)
        if length > self.context.settings.max_request_bytes:
            self.close_connection = True
            raise ApiError(
                "request_too_large",
                "request body exceeds safe limits",
                status=413,
            )
        raw = self.rfile.read(length)
        if len(raw) != length:
            self.close_connection = True
            raise ApiError(
                "incomplete_request_body",
                "request body ended early",
                status=400,
            )
        value = _decode_json(raw)
        validate_json_limits(value, self.context.settings.json_limits)
        return value

    def _idempotency_key(self) -> str:
        values = self.headers.get_all("Idempotency-Key") or []
        if len(values) != 1:
            raise ApiError(
                "idempotency_key_required",
                "exactly one Idempotency-Key header is required for mutations",
                status=400,
            )
        return canonical_idempotency_key(values[0])

    def _require_owned_run(self, subject: str, run_id: str) -> None:
        try:
            owned = self.context.idempotency.subject_owns_run(
                subject=subject,
                run_id=run_id,
            )
        except IdempotencyError as exc:
            raise ApiError(
                "state_unavailable",
                "service ownership state is unavailable",
                status=503,
                retryable=True,
            ) from exc
        if not owned:
            raise ApiError("run_not_found", "run was not found", status=404)

    def _mutation(
        self,
        *,
        subject: str,
        request_id: str,
        method: str,
        path: str,
        payload: Any,
        run_id: str | None,
        function: Callable[[], dict[str, Any]],
    ) -> None:
        key = self._idempotency_key()
        fingerprint = canonical_fingerprint(method, path, payload)
        try:
            claim = self.context.idempotency.claim(
                subject=subject,
                key=key,
                method=method,
                path=path,
                request_fingerprint=fingerprint,
                run_id=run_id,
            )
        except IdempotencyError as exc:
            raise ApiError(
                "idempotency_unavailable",
                "idempotency state is unavailable",
                status=503,
                retryable=True,
            ) from exc
        if claim.disposition == "conflict":
            raise ApiError(
                "idempotency_conflict",
                "Idempotency-Key was already used for a different request",
                status=409,
            )
        if claim.disposition == "replay":
            self._reply(
                int(claim.status_code or 200),
                claim.response
                or {
                    "ok": False,
                    "request_id": request_id,
                    "error": {"code": "idempotency_corrupt"},
                },
                request_id=request_id,
                extra_headers={"Idempotency-Replayed": "true"},
            )
            return
        if claim.disposition == "in_progress":
            self._reply(
                202,
                {
                    "ok": True,
                    "request_id": request_id,
                    "data": {"status": "in_progress", "run_id": claim.run_id},
                },
                request_id=request_id,
            )
            return
        if claim.disposition == "ambiguous":
            raise ApiError(
                "operation_outcome_unknown",
                "a prior attempt has an ambiguous outcome; query/reconcile the run before retrying",
                status=409,
                recovery_required=True,
                ambiguous=True,
            )
        assert claim.disposition == "new"
        if run_id is not None:
            try:
                self.context.idempotency.bind_run(subject=subject, run_id=run_id)
            except IdempotencyError as exc:
                self.context.idempotency.abandon(subject=subject, key=key)
                raise ApiError(
                    "run_conflict",
                    "run identity is unavailable for this subject",
                    status=409,
                ) from exc
        result = self.context.coordinator.execute(
            subject=subject,
            key=key,
            run_id=run_id,
            request_id=request_id,
            function=function,
        )
        headers = {"Retry-After": "1"} if result.status in {202, 503} else None
        self._reply(
            result.status,
            result.payload,
            request_id=request_id,
            extra_headers=headers,
        )

    def _route_run_id(self, path: str, suffix: str | None = None) -> str | None:
        prefix = "/v1/runs/"
        if not path.startswith(prefix):
            return None
        remainder = path[len(prefix) :]
        if suffix is None:
            if "/" in remainder:
                return None
            return canonical_id(remainder, "run_id")
        trailer = "/" + suffix
        if not remainder.endswith(trailer):
            return None
        run_id = remainder[: -len(trailer)]
        if "/" in run_id:
            return None
        return canonical_id(run_id, "run_id")

    def do_GET(self) -> None:
        request_id = self._request_id()
        try:
            self._header_bounds()
            path = self._path()
            if path == "/livez":
                payload = self.context.health.liveness()
                self._reply(
                    200 if payload.get("ok") else 503,
                    payload,
                    request_id=request_id,
                )
                return
            if path in {"/readyz", "/healthz"}:
                payload = self.context.health.readiness()
                self._reply(
                    200 if payload.get("ok") else 503,
                    payload,
                    request_id=request_id,
                )
                return
            subject = self._subject()
            if path == "/v1/version":
                self._reply(
                    200,
                    {
                        "ok": True,
                        "request_id": request_id,
                        "data": {
                            "api_version": "v1",
                            "runtime_version": _package_version(),
                        },
                    },
                    request_id=request_id,
                )
                return
            if path == "/v1/capabilities":
                self._reply(
                    200,
                    {
                        "ok": True,
                        "request_id": request_id,
                        "data": {
                            "submit": True,
                            "query": True,
                            "resume": True,
                            "cancel_at_safe_points": True,
                            "approval_decisions": True,
                            "recovery_resolution": True,
                            "durable_idempotency": True,
                            "streaming": False,
                            "raw_mcp_gateway": False,
                        },
                    },
                    request_id=request_id,
                )
                return
            run_id = self._route_run_id(path)
            if run_id is not None:
                self._require_owned_run(subject, run_id)
                data = self.context.backend.get_run(run_id, subject=subject)
                self._reply(
                    200,
                    {"ok": True, "request_id": request_id, "data": data},
                    request_id=request_id,
                )
                return
            raise ApiError("not_found", "endpoint was not found", status=404)
        except ApiError as exc:
            extra = {"WWW-Authenticate": "Bearer"} if exc.status == 401 else None
            self._reply(
                exc.status,
                exc.envelope(request_id),
                request_id=request_id,
                extra_headers=extra,
            )
        except (socket.timeout, TimeoutError):
            self.close_connection = True
            self._error(
                ApiError(
                    "request_timeout",
                    "request timed out",
                    status=408,
                    retryable=True,
                ),
                request_id=request_id,
            )
        except BaseException:
            self.close_connection = True
            self._error(
                ApiError("internal_error", "request failed safely", status=500),
                request_id=request_id,
            )

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_POST(self) -> None:
        request_id = self._request_id()
        body_consumed = False
        try:
            self._header_bounds()
            path = self._path()
            if self.context.health.readiness().get("ok") is not True:
                self.close_connection = True
                raise ApiError(
                    "not_ready",
                    "service is not ready to accept work",
                    status=503,
                    retryable=True,
                )
            subject = self._subject()
            payload = self._read_json()
            body_consumed = True
            if path == "/v1/runs":
                task_input = validate_task_input(payload)
                run_id = f"run:{task_input['task']['task_id']}"
                canonical_id(run_id, "run_id")
                self._mutation(
                    subject=subject,
                    request_id=request_id,
                    method="POST",
                    path=path,
                    payload=task_input,
                    run_id=run_id,
                    function=lambda: self.context.backend.submit(
                        task_input,
                        subject=subject,
                    ),
                )
                return
            run_id = self._route_run_id(path, "resume")
            if run_id is not None:
                command = validate_empty_command(payload)
                self._require_owned_run(subject, run_id)
                self._mutation(
                    subject=subject,
                    request_id=request_id,
                    method="POST",
                    path=path,
                    payload=command,
                    run_id=run_id,
                    function=lambda: self.context.backend.resume(
                        run_id,
                        subject=subject,
                    ),
                )
                return
            run_id = self._route_run_id(path, "cancel")
            if run_id is not None:
                command = validate_empty_command(payload)
                self._require_owned_run(subject, run_id)
                self._mutation(
                    subject=subject,
                    request_id=request_id,
                    method="POST",
                    path=path,
                    payload=command,
                    run_id=run_id,
                    function=lambda: self.context.backend.cancel(
                        run_id,
                        subject=subject,
                    ),
                )
                return
            run_id = self._route_run_id(path, "approval")
            if run_id is not None:
                decision = validate_approval_decision(payload)
                self._require_owned_run(subject, run_id)
                self._mutation(
                    subject=subject,
                    request_id=request_id,
                    method="POST",
                    path=path,
                    payload=decision,
                    run_id=run_id,
                    function=lambda: self.context.backend.decide_approval(
                        run_id,
                        decision,
                        subject=subject,
                    ),
                )
                return
            run_id = self._route_run_id(path, "recovery")
            if run_id is not None:
                resolution = validate_recovery_resolution(payload)
                if resolution.get("run_id") != run_id:
                    raise ApiError(
                        "recovery_conflict",
                        "recovery run_id does not match route",
                        status=409,
                    )
                self._require_owned_run(subject, run_id)
                self._mutation(
                    subject=subject,
                    request_id=request_id,
                    method="POST",
                    path=path,
                    payload=resolution,
                    run_id=run_id,
                    function=lambda: self.context.backend.resolve_recovery(
                        run_id,
                        resolution,
                        subject=subject,
                    ),
                )
                return
            raise ApiError("not_found", "endpoint was not found", status=404)
        except ApiError as exc:
            if not body_consumed:
                self.close_connection = True
            extra = {"WWW-Authenticate": "Bearer"} if exc.status == 401 else None
            self._reply(
                exc.status,
                exc.envelope(request_id),
                request_id=request_id,
                extra_headers=extra,
            )
        except (socket.timeout, TimeoutError):
            self.close_connection = True
            self._error(
                ApiError(
                    "request_timeout",
                    "request timed out",
                    status=408,
                    retryable=True,
                ),
                request_id=request_id,
            )
        except BaseException:
            self.close_connection = True
            self._error(
                ApiError("internal_error", "request failed safely", status=500),
                request_id=request_id,
            )

    def do_PUT(self) -> None:
        self._method_not_allowed()

    def do_PATCH(self) -> None:
        self._method_not_allowed()

    def do_DELETE(self) -> None:
        self._method_not_allowed()

    def _method_not_allowed(self) -> None:
        request_id = self._request_id()
        self._reply(
            405,
            ApiError(
                "method_not_allowed",
                "HTTP method is not allowed",
                status=405,
            ).envelope(request_id),
            request_id=request_id,
            extra_headers={"Allow": "GET, HEAD, POST"},
        )


def create_gateway_server(
    config: DeploymentConfig,
    settings: GatewaySettings,
    backend: ServiceBackend,
    *,
    idempotency: SQLiteIdempotencyStore | None = None,
    operations: OperationalRuntime | None = None,
) -> tuple[_GatewayHTTPServer, GatewayContext]:
    resolved_backend = _validate_backend(backend)
    resolved_idempotency = idempotency or SQLiteIdempotencyStore(settings.idempotency_path)
    resolved_operations = operations or _service_operations(config)
    limits = resolved_operations.capacity.limits
    if (
        limits.active_runs != config.max_concurrency
        or limits.queued_runs != config.queue_limit
    ):
        raise GatewayConfigError("service operational capacity must match deployment limits")

    health = HealthRegistry()
    authenticator = _Authenticator(config, settings)
    if config.environment in {"staging", "production"}:
        authenticator.require_available()
    if settings.auth_mode == "bearer":
        health.register_dependency(
            DependencyCheck("service_auth", authenticator.ready, critical=True)
        )
    health.register_dependency(
        DependencyCheck("idempotency", resolved_idempotency.ready, critical=True)
    )
    health.register_dependency(
        DependencyCheck("backend", resolved_backend.readiness, critical=True)
    )
    health.register_dependency(
        DependencyCheck(
            "operations",
            lambda: _operations_dependency(resolved_operations),
            critical=False,
        )
    )

    coordinator = _OperationCoordinator(
        max_concurrency=config.max_concurrency,
        wait_timeout_seconds=config.request_timeout_seconds,
        shutdown_timeout_seconds=config.graceful_shutdown_seconds,
        idempotency=resolved_idempotency,
    )
    context = GatewayContext(
        config=config,
        settings=settings,
        backend=resolved_backend,
        idempotency=resolved_idempotency,
        health=health,
        authenticator=authenticator,
        operations=resolved_operations,
        coordinator=coordinator,
    )
    server = _GatewayHTTPServer(
        (config.bind_host, config.bind_port),
        _Handler,
        context=context,
    )
    if config.tls_mode == "direct":
        assert config.tls_cert_file and config.tls_key_file
        tls = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        tls.load_cert_chain(config.tls_cert_file, config.tls_key_file)
        server.socket = tls.wrap_socket(server.socket, server_side=True)
    health.set_accepting_work(True)
    return server, context


def run_service(
    config: DeploymentConfig | None = None,
    settings: GatewaySettings | None = None,
    backend: ServiceBackend | None = None,
) -> None:
    resolved_config = config or load_deployment_config()
    validate_runtime_paths(resolved_config)
    resolved_settings = settings or load_gateway_settings(resolved_config)
    resolved_backend = backend
    if resolved_backend is None:
        if resolved_settings.backend_factory is None:
            raise GatewayConfigError(
                "production service entrypoint requires an application-owned backend factory"
            )
        resolved_backend = load_backend_factory(
            resolved_settings.backend_factory,
            resolved_config,
            resolved_settings,
        )
    idempotency = SQLiteIdempotencyStore(resolved_settings.idempotency_path)
    idempotency.recover_orphans()
    server, context = create_gateway_server(
        resolved_config,
        resolved_settings,
        resolved_backend,
        idempotency=idempotency,
    )
    shutdown = GracefulShutdown()

    def drain(_reason: str) -> None:
        context.health.begin_shutdown()
        threading.Thread(
            target=server.shutdown,
            name="manager-gateway-shutdown",
            daemon=True,
        ).start()

    shutdown.add_drain_callback(drain)
    shutdown.install_signal_handlers()
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        context.health.begin_shutdown()
        server.server_close()
        close = getattr(resolved_backend, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
        context.health.mark_dead()
