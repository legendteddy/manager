from __future__ import annotations

import hmac
import json
import os
import re
import socket
import ssl
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Mapping

from ..capacity import CapacityGate, OverloadedError
from ..deployment import DependencyCheck, GracefulShutdown, HealthRegistry, validate_runtime_paths
from ..deployment.config import DeploymentConfig, load_deployment_config
from ..engine import run as run_control_plane
from ..security import MountedFileSecretProvider, SecurityBoundaryError
from ..state import SQLiteRunStore

_SERVICE_PREFIX = "MANAGER_SERVICE_"
_ALLOWED_SERVICE_ENV = {
    "MANAGER_SERVICE_AUTH_MODE",
    "MANAGER_SERVICE_AUTH_SECRET_NAME",
    "MANAGER_SERVICE_MAX_REQUEST_BYTES",
}
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:/-]{1,128}$")
_TASK_FIELDS = {
    "task_id",
    "objective",
    "decision_context",
    "inputs",
    "classification",
    "requested_capabilities",
    "authority",
    "extensions",
}
_CLASSIFICATION_FIELDS = {
    "materiality",
    "consequence",
    "uncertainty",
    "reversibility",
    "sensitivity",
}
_INPUT_FIELDS = {"task", "prior_state", "untrusted_content"}


class ServiceConfigError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ManagerServiceSettings:
    auth_mode: str
    auth_secret_name: str
    max_request_bytes: int


def load_service_settings(
    config: DeploymentConfig,
    *,
    environ: Mapping[str, str] | None = None,
) -> ManagerServiceSettings:
    env = os.environ if environ is None else environ
    unknown = sorted(
        key for key in env if key.startswith(_SERVICE_PREFIX) and key not in _ALLOWED_SERVICE_ENV
    )
    if unknown:
        raise ServiceConfigError(f"unknown service environment settings: {', '.join(unknown)}")

    default_mode = "bearer" if config.environment in {"staging", "production"} else "none"
    auth_mode = env.get("MANAGER_SERVICE_AUTH_MODE", default_mode).strip().lower()
    if auth_mode not in {"none", "bearer"}:
        raise ServiceConfigError("MANAGER_SERVICE_AUTH_MODE must be none or bearer")
    if config.environment in {"staging", "production"} and auth_mode != "bearer":
        raise ServiceConfigError(f"{config.environment} service authentication must use bearer mode")

    auth_secret_name = env.get("MANAGER_SERVICE_AUTH_SECRET_NAME", "service-auth-token").strip()
    if not auth_secret_name or re.fullmatch(r"[A-Za-z0-9_.-]+", auth_secret_name) is None:
        raise ServiceConfigError("MANAGER_SERVICE_AUTH_SECRET_NAME is invalid")
    if auth_mode == "bearer" and not config.secrets_dir:
        raise ServiceConfigError("bearer service authentication requires deployment secrets_dir")

    raw_limit = env.get("MANAGER_SERVICE_MAX_REQUEST_BYTES", str(1024 * 1024))
    try:
        max_request_bytes = int(raw_limit)
    except (TypeError, ValueError) as exc:
        raise ServiceConfigError("MANAGER_SERVICE_MAX_REQUEST_BYTES must be an integer") from exc
    if not 1024 <= max_request_bytes <= 16 * 1024 * 1024:
        raise ServiceConfigError("MANAGER_SERVICE_MAX_REQUEST_BYTES must be between 1024 and 16777216")

    return ManagerServiceSettings(
        auth_mode=auth_mode,
        auth_secret_name=auth_secret_name,
        max_request_bytes=max_request_bytes,
    )


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON member")
        value[key] = item
    return value


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is not permitted: {value}")


def _decode_json(raw: bytes) -> Any:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("request body must be UTF-8") from exc
    try:
        return json.loads(text, object_pairs_hook=_strict_pairs, parse_constant=_reject_constant)
    except (json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise ValueError("request body is not strict JSON") from exc


def _require_text(value: Any, field: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")
    if len(value) > maximum or "\x00" in value:
        raise ValueError(f"{field} is invalid")
    return value


def _validate_task_input(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("request must be an object")
    unknown_input = sorted(set(value) - _INPUT_FIELDS)
    if unknown_input:
        raise ValueError(f"request has unknown fields: {', '.join(unknown_input)}")
    task = value.get("task")
    if not isinstance(task, dict):
        raise ValueError("request.task must be an object")
    unknown_task = sorted(set(task) - _TASK_FIELDS)
    if unknown_task:
        raise ValueError(f"task has unknown fields: {', '.join(unknown_task)}")
    _require_text(task.get("task_id"), "task.task_id", maximum=256)
    _require_text(task.get("objective"), "task.objective", maximum=65536)
    classification = task.get("classification")
    if not isinstance(classification, dict):
        raise ValueError("task.classification must be an object")
    unknown_classification = sorted(set(classification) - _CLASSIFICATION_FIELDS)
    if unknown_classification:
        raise ValueError(
            f"task.classification has unknown fields: {', '.join(unknown_classification)}"
        )
    required = ("materiality", "consequence", "uncertainty")
    if any(field not in classification for field in required):
        raise ValueError("task.classification is missing required fields")
    if classification["materiality"] not in {"routine", "material"}:
        raise ValueError("task.classification.materiality is invalid")
    if classification["consequence"] not in {"low", "medium", "high", "critical"}:
        raise ValueError("task.classification.consequence is invalid")
    if classification["uncertainty"] not in {"low", "medium", "high"}:
        raise ValueError("task.classification.uncertainty is invalid")
    if "reversibility" in classification and classification["reversibility"] not in {
        "reversible", "partially_reversible", "irreversible", "unknown"
    }:
        raise ValueError("task.classification.reversibility is invalid")
    if "sensitivity" in classification and classification["sensitivity"] not in {
        "public", "internal", "sensitive", "unknown"
    }:
        raise ValueError("task.classification.sensitivity is invalid")
    capabilities = task.get("requested_capabilities")
    if capabilities is not None:
        if not isinstance(capabilities, list) or not all(
            isinstance(item, str) and item for item in capabilities
        ) or len(set(capabilities)) != len(capabilities):
            raise ValueError("task.requested_capabilities must contain unique non-empty strings")
    for name in ("inputs", "authority", "extensions"):
        if name in task and not isinstance(task[name], dict):
            raise ValueError(f"task.{name} must be an object")
    if "decision_context" in task and not isinstance(task["decision_context"], str):
        raise ValueError("task.decision_context must be text")
    prior = value.get("prior_state")
    if prior is not None and not isinstance(prior, dict):
        raise ValueError("prior_state must be an object")
    untrusted = value.get("untrusted_content")
    if untrusted is not None:
        if not isinstance(untrusted, list) or len(untrusted) > 64:
            raise ValueError("untrusted_content must be a bounded list")
        for item in untrusted:
            if not isinstance(item, str) or not item or len(item) > 65536:
                raise ValueError("untrusted_content items must be bounded non-empty text")
    return value


class _BearerAuthenticator:
    def __init__(self, config: DeploymentConfig, settings: ManagerServiceSettings) -> None:
        self.mode = settings.auth_mode
        self.secret_name = settings.auth_secret_name
        self.provider = (
            MountedFileSecretProvider(config.secrets_dir)
            if self.mode == "bearer" and config.secrets_dir is not None
            else None
        )

    def ready(self) -> bool | tuple[bool, str]:
        if self.mode == "none":
            return True
        try:
            assert self.provider is not None
            self.provider.acquire(self.secret_name).reveal()
        except Exception:
            return False, "service credential unavailable"
        return True

    def require_available(self) -> None:
        result = self.ready()
        ok = result if isinstance(result, bool) else result[0]
        if not ok:
            raise ServiceConfigError("service authentication credential is unavailable")

    def authorized(self, header: str | None) -> bool:
        if self.mode == "none":
            return True
        if not isinstance(header, str) or len(header) > 16384 or not header.startswith("Bearer "):
            return False
        supplied = header[7:].encode("utf-8", errors="strict")
        if not supplied:
            return False
        try:
            assert self.provider is not None
            expected = self.provider.acquire(self.secret_name).reveal()
        except (SecurityBoundaryError, OSError, ValueError):
            return False
        return hmac.compare_digest(supplied, expected)


class _Telemetry:
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self._lock = threading.Lock()

    def event(self, name: str, **fields: Any) -> None:
        if self.mode != "json_stdout":
            return
        payload = {"event": name, **fields}
        line = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        with self._lock:
            print(line, file=sys.stdout, flush=True)


@dataclass(slots=True)
class ManagerServiceContext:
    config: DeploymentConfig
    settings: ManagerServiceSettings
    health: HealthRegistry
    authenticator: _BearerAuthenticator
    run_gate: CapacityGate
    telemetry: _Telemetry


class _BoundedHTTPServer(HTTPServer):
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        *,
        context: ManagerServiceContext,
    ) -> None:
        self.context = context
        worker_count = context.config.max_concurrency + 2
        queue_limit = context.config.queue_limit
        self.request_queue_size = max(5, min(worker_count + queue_limit, 1024))
        self._slots = threading.BoundedSemaphore(worker_count + queue_limit)
        self._executor = ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="manager-http",
        )
        super().__init__(server_address, handler, bind_and_activate=True)

    def process_request(self, request: socket.socket, client_address: tuple[str, int]) -> None:
        if not self._slots.acquire(blocking=False):
            try:
                request.sendall(
                    b"HTTP/1.1 503 Service Unavailable\r\n"
                    b"Content-Type: application/json\r\n"
                    b"Content-Length: 38\r\n"
                    b"Connection: close\r\n\r\n"
                    b'{"error":"service_overloaded","ok":false}'
                )
            except OSError:
                pass
            finally:
                self.shutdown_request(request)
            return
        try:
            self._executor.submit(self._process_request_worker, request, client_address)
        except Exception:
            self._slots.release()
            self.shutdown_request(request)
            raise

    def _process_request_worker(self, request: socket.socket, client_address: tuple[str, int]) -> None:
        try:
            self.finish_request(request, client_address)
        except Exception as exc:
            self.context.telemetry.event("http_handler_failure", error_type=type(exc).__name__)
        finally:
            try:
                self.shutdown_request(request)
            finally:
                self._slots.release()

    def handle_error(self, request: Any, client_address: Any) -> None:
        self.context.telemetry.event("http_server_failure")

    def server_close(self) -> None:
        try:
            super().server_close()
        finally:
            self._executor.shutdown(wait=True, cancel_futures=False)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "manager"
    sys_version = ""

    @property
    def context(self) -> ManagerServiceContext:
        return self.server.context  # type: ignore[attr-defined]

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(self.context.config.request_timeout_seconds)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def version_string(self) -> str:
        return "manager"

    def _request_id(self) -> str:
        candidate = self.headers.get("X-Request-ID")
        if candidate and _REQUEST_ID.fullmatch(candidate):
            return candidate
        return f"request:{uuid.uuid4()}"

    def _reply(
        self,
        status: int,
        payload: dict[str, Any],
        *,
        request_id: str,
        extra_headers: Mapping[str, str] | None = None,
    ) -> None:
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Request-ID", request_id)
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(raw)
            except OSError:
                pass

    def _health(self, *, readiness: bool) -> None:
        request_id = self._request_id()
        payload = self.context.health.readiness() if readiness else self.context.health.liveness()
        status = 200 if payload.get("ok") else 503
        self._reply(status, payload, request_id=request_id)

    def do_GET(self) -> None:
        if self.path == "/livez":
            self._health(readiness=False)
            return
        if self.path in {"/readyz", "/healthz"}:
            self._health(readiness=True)
            return
        request_id = self._request_id()
        self._reply(404, {"ok": False, "error": "not_found"}, request_id=request_id)

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_POST(self) -> None:
        request_id = self._request_id()
        started = time.monotonic()
        status = 500
        try:
            if self.path != "/v1/run":
                status = 404
                self._reply(status, {"ok": False, "error": "not_found"}, request_id=request_id)
                return
            if self.context.health.readiness().get("ok") is not True:
                status = 503
                self._reply(status, {"ok": False, "error": "not_ready"}, request_id=request_id)
                return
            if not self.context.authenticator.authorized(self.headers.get("Authorization")):
                status = 401
                self._reply(
                    status,
                    {"ok": False, "error": "unauthorized"},
                    request_id=request_id,
                    extra_headers={"WWW-Authenticate": "Bearer"},
                )
                return
            transfer_encoding = self.headers.get("Transfer-Encoding")
            if transfer_encoding:
                status = 400
                self._reply(status, {"ok": False, "error": "unsupported_transfer_encoding"}, request_id=request_id)
                return
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/json":
                status = 415
                self._reply(status, {"ok": False, "error": "content_type_must_be_application_json"}, request_id=request_id)
                return
            raw_length = self.headers.get("Content-Length")
            if raw_length is None:
                status = 411
                self._reply(status, {"ok": False, "error": "content_length_required"}, request_id=request_id)
                return
            try:
                length = int(raw_length)
            except ValueError:
                length = -1
            if length < 0:
                status = 400
                self._reply(status, {"ok": False, "error": "invalid_content_length"}, request_id=request_id)
                return
            if length > self.context.settings.max_request_bytes:
                status = 413
                self._reply(status, {"ok": False, "error": "request_too_large"}, request_id=request_id)
                return
            raw = self.rfile.read(length)
            if len(raw) != length:
                status = 400
                self._reply(status, {"ok": False, "error": "incomplete_request_body"}, request_id=request_id)
                return
            try:
                task_input = _validate_task_input(_decode_json(raw))
            except ValueError:
                status = 400
                self._reply(status, {"ok": False, "error": "invalid_request"}, request_id=request_id)
                return
            try:
                with self.context.run_gate.acquire():
                    output = run_control_plane(task_input)
            except OverloadedError:
                status = 503
                self._reply(
                    status,
                    {"ok": False, "error": "service_overloaded"},
                    request_id=request_id,
                    extra_headers={"Retry-After": "1"},
                )
                return
            status = 200
            self._reply(status, {"ok": True, "output": output}, request_id=request_id)
        except (socket.timeout, TimeoutError):
            status = 408
            self._reply(status, {"ok": False, "error": "request_timeout"}, request_id=request_id)
        except Exception as exc:
            status = 500
            self.context.telemetry.event(
                "http_request_failure",
                request_id=request_id,
                error_type=type(exc).__name__,
            )
            try:
                self._reply(status, {"ok": False, "error": "internal_error"}, request_id=request_id)
            except Exception:
                pass
        finally:
            self.context.telemetry.event(
                "http_request",
                request_id=request_id,
                method=self.command,
                path=self.path,
                status=status,
                duration_ms=max(0, int((time.monotonic() - started) * 1000)),
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
            {"ok": False, "error": "method_not_allowed"},
            request_id=request_id,
            extra_headers={"Allow": "GET, HEAD, POST"},
        )


def _sqlite_dependency(path: str) -> bool | tuple[bool, str]:
    try:
        store = SQLiteRunStore(path)
        store.load("__manager_health_probe_missing__")
    except Exception as exc:
        return False, f"state backend unavailable ({type(exc).__name__})"
    return True


def create_service_server(
    config: DeploymentConfig,
    settings: ManagerServiceSettings,
) -> tuple[_BoundedHTTPServer, ManagerServiceContext]:
    health = HealthRegistry()
    authenticator = _BearerAuthenticator(config, settings)
    if config.environment in {"staging", "production"}:
        authenticator.require_available()
    if settings.auth_mode == "bearer":
        health.register_dependency(DependencyCheck("service_auth", authenticator.ready, critical=True))
    if config.state_backend == "sqlite":
        assert config.sqlite_path is not None
        # Initialize/migrate before readiness can become true.
        SQLiteRunStore(config.sqlite_path)
        health.register_dependency(
            DependencyCheck(
                "state",
                lambda: _sqlite_dependency(config.sqlite_path or ""),
                critical=True,
            )
        )

    context = ManagerServiceContext(
        config=config,
        settings=settings,
        health=health,
        authenticator=authenticator,
        run_gate=CapacityGate("active_runs", config.max_concurrency),
        telemetry=_Telemetry(config.telemetry_mode),
    )
    server = _BoundedHTTPServer((config.bind_host, config.bind_port), _Handler, context=context)
    if config.tls_mode == "direct":
        assert config.tls_cert_file and config.tls_key_file
        tls = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        tls.load_cert_chain(config.tls_cert_file, config.tls_key_file)
        server.socket = tls.wrap_socket(server.socket, server_side=True)
    health.set_accepting_work(True)
    return server, context


def run_service(
    config: DeploymentConfig | None = None,
    settings: ManagerServiceSettings | None = None,
) -> None:
    resolved_config = config or load_deployment_config()
    validate_runtime_paths(resolved_config)
    resolved_settings = settings or load_service_settings(resolved_config)
    server, context = create_service_server(resolved_config, resolved_settings)
    shutdown = GracefulShutdown()

    def drain(reason: str) -> None:
        context.health.begin_shutdown()
        context.telemetry.event("service_draining", reason=reason)
        threading.Thread(target=server.shutdown, name="manager-http-shutdown", daemon=True).start()

    shutdown.add_drain_callback(drain)
    shutdown.install_signal_handlers()
    context.telemetry.event(
        "service_started",
        environment=resolved_config.environment,
        bind_host=resolved_config.bind_host,
        bind_port=resolved_config.bind_port,
        tls_mode=resolved_config.tls_mode,
        auth_mode=resolved_settings.auth_mode,
    )
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        context.health.begin_shutdown()
        server.server_close()
        context.health.mark_dead()
        context.telemetry.event("service_stopped", reason=shutdown.reason or "server_exit")
