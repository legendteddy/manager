from __future__ import annotations

import json
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from manager_runtime.deployment.config import DeploymentConfig
from manager_runtime.service.api import ApiError, JsonLimits
from manager_runtime.service.gateway import (
    GatewayConfigError,
    GatewaySettings,
    create_gateway_server,
    load_gateway_settings,
)
from manager_runtime.service.idempotency import SQLiteIdempotencyStore, canonical_fingerprint


def config(
    base: str,
    *,
    environment: str = "testing",
    timeout: float = 1.0,
    grace: float = 0.2,
    secrets: str | None = None,
) -> DeploymentConfig:
    return DeploymentConfig(
        environment=environment,
        bind_host="127.0.0.1",
        bind_port=0,
        state_backend="memory" if environment == "testing" else "sqlite",
        sqlite_path=None if environment == "testing" else str(Path(base) / "state.sqlite3"),
        instance_count=1,
        max_concurrency=2,
        queue_limit=2,
        request_timeout_seconds=timeout,
        provider_timeout_seconds=0.5,
        mcp_timeout_seconds=0.5,
        graceful_shutdown_seconds=grace,
        tls_mode="off" if environment == "testing" else "external",
        tls_cert_file=None,
        tls_key_file=None,
        telemetry_mode="off" if environment == "testing" else "json_stdout",
        data_dir=base,
        tmp_dir=base,
        secrets_dir=secrets,
        read_only_root=environment != "testing",
    )


def settings(
    base: str,
    *,
    auth_mode: str = "none",
    max_request: int = 1024 * 1024,
    max_header: int = 32 * 1024,
) -> GatewaySettings:
    return GatewaySettings(
        auth_mode=auth_mode,
        auth_secret_name="service-auth-token",
        backend_factory=None,
        idempotency_path=str(Path(base) / "api.sqlite3"),
        max_request_bytes=max_request,
        max_header_bytes=max_header,
        json_limits=JsonLimits(
            max_depth=16,
            max_nodes=2048,
            max_string_chars=65536,
        ),
    )


def task(
    task_id: str = "gateway-test",
    objective: str = "Summarize synthetic input",
) -> dict:
    return {
        "task": {
            "task_id": task_id,
            "objective": objective,
            "classification": {
                "materiality": "routine",
                "consequence": "low",
                "uncertainty": "low",
                "reversibility": "reversible",
                "sensitivity": "public",
            },
        }
    }


class SyntheticBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.runs: dict[str, dict] = {}
        self.ready_value: bool | tuple[bool, str] = True
        self.block = False
        self.entered = threading.Event()
        self.release = threading.Event()
        self.fail: Exception | None = None

    def readiness(self):
        return self.ready_value

    def submit(self, payload: dict, *, subject: str) -> dict:
        run_id = f"run:{payload['task']['task_id']}"
        self.calls.append(("submit", run_id))
        self.entered.set()
        if self.block:
            self.release.wait(2.0)
        if self.fail is not None:
            raise self.fail
        state = {
            "run_id": run_id,
            "task_id": payload["task"]["task_id"],
            "status": "completed",
            "revision": 1,
            "created_at": "2026-10-09T00:00:00Z",
            "updated_at": "2026-10-09T00:00:00Z",
            "recovery_required": False,
            "result": {"status": "completed", "finding": "synthetic"},
        }
        self.runs[run_id] = state
        return dict(state)

    def get_run(self, run_id: str, *, subject: str) -> dict:
        self.calls.append(("get", run_id))
        if run_id not in self.runs:
            raise ApiError("run_not_found", "run was not found", status=404)
        return dict(self.runs[run_id])

    def resume(self, run_id: str, *, subject: str) -> dict:
        self.calls.append(("resume", run_id))
        return self.get_run(run_id, subject=subject)

    def cancel(self, run_id: str, *, subject: str) -> dict:
        self.calls.append(("cancel", run_id))
        value = self.get_run(run_id, subject=subject)
        value["status"] = "cancelled"
        self.runs[run_id] = value
        return value

    def decide_approval(self, run_id: str, decision: dict, *, subject: str) -> dict:
        self.calls.append(("approval", run_id))
        return self.get_run(run_id, subject=subject)

    def resolve_recovery(self, run_id: str, resolution: dict, *, subject: str) -> dict:
        self.calls.append(("recovery", run_id))
        return self.get_run(run_id, subject=subject)


class RunningGateway:
    def __init__(
        self,
        cfg: DeploymentConfig,
        cfg_settings: GatewaySettings,
        backend: SyntheticBackend,
        *,
        store: SQLiteIdempotencyStore | None = None,
    ) -> None:
        self.server, self.context = create_gateway_server(
            cfg,
            cfg_settings,
            backend,
            idempotency=store,
        )
        host, port = self.server.server_address[:2]
        self.host = str(host)
        self.port = int(port)
        self.base = f"http://{host}:{port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.closed = False

    def __enter__(self):
        self.thread.start()
        return self

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.context.health.begin_shutdown()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: object | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 2.0,
    ):
        raw = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
        request = urllib.request.Request(
            self.base + path,
            data=raw,
            method=method,
            headers=headers or {},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, dict(response.headers), json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), json.loads(exc.read())

    def raw_exchange(self, raw: bytes) -> bytes:
        chunks: list[bytes] = []
        with socket.create_connection((self.host, self.port), timeout=2) as connection:
            connection.settimeout(1)
            connection.sendall(raw)
            try:
                connection.shutdown(socket.SHUT_WR)
            except OSError:
                pass
            while True:
                try:
                    chunk = connection.recv(65536)
                except socket.timeout:
                    break
                if not chunk:
                    break
                chunks.append(chunk)
        return b"".join(chunks)


class ServiceGatewayTests(unittest.TestCase):
    def test_production_requires_backend_factory_and_bearer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cfg = config(directory, environment="production", secrets=directory)
            with self.assertRaisesRegex(GatewayConfigError, "BACKEND_FACTORY"):
                load_gateway_settings(
                    cfg,
                    environ={"MANAGER_SERVICE_AUTH_MODE": "bearer"},
                )
            with self.assertRaisesRegex(GatewayConfigError, "bearer"):
                load_gateway_settings(
                    cfg,
                    environ={
                        "MANAGER_SERVICE_AUTH_MODE": "none",
                        "MANAGER_SERVICE_BACKEND_FACTORY": "synthetic:factory",
                    },
                )

    def test_health_version_capabilities_and_no_streaming_claim(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backend = SyntheticBackend()
            with RunningGateway(config(directory), settings(directory), backend) as service:
                status, _, live = service.request("/livez")
                self.assertEqual(200, status)
                self.assertTrue(live["ok"])
                status, _, ready = service.request("/readyz")
                self.assertEqual(200, status)
                self.assertTrue(ready["ok"])
                status, _, version = service.request("/v1/version")
                self.assertEqual(200, status)
                self.assertEqual("v1", version["data"]["api_version"])
                status, _, caps = service.request("/v1/capabilities")
                self.assertEqual(200, status)
                self.assertFalse(caps["data"]["streaming"])
                self.assertFalse(caps["data"]["raw_mcp_gateway"])

    def test_duplicate_submission_replays_without_second_backend_call(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backend = SyntheticBackend()
            with RunningGateway(config(directory), settings(directory), backend) as service:
                headers = {
                    "Content-Type": "application/json",
                    "Idempotency-Key": "submit-1",
                }
                first = service.request(
                    "/v1/runs",
                    method="POST",
                    payload=task(),
                    headers=headers,
                )
                second = service.request(
                    "/v1/runs",
                    method="POST",
                    payload=task(),
                    headers=headers,
                )
                self.assertEqual(200, first[0])
                self.assertEqual(200, second[0])
                self.assertEqual("true", second[1].get("Idempotency-Replayed"))
                self.assertEqual(
                    1,
                    sum(1 for kind, _ in backend.calls if kind == "submit"),
                )
                status, _, run = service.request("/v1/runs/run:gateway-test")
                self.assertEqual(200, status)
                self.assertEqual("completed", run["data"]["status"])

    def test_same_idempotency_key_with_different_request_is_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backend = SyntheticBackend()
            with RunningGateway(config(directory), settings(directory), backend) as service:
                headers = {
                    "Content-Type": "application/json",
                    "Idempotency-Key": "same-key",
                }
                self.assertEqual(
                    200,
                    service.request(
                        "/v1/runs",
                        method="POST",
                        payload=task(),
                        headers=headers,
                    )[0],
                )
                status, _, payload = service.request(
                    "/v1/runs",
                    method="POST",
                    payload=task(objective="different"),
                    headers=headers,
                )
                self.assertEqual(409, status)
                self.assertEqual("idempotency_conflict", payload["error"]["code"])
                self.assertEqual(
                    1,
                    sum(1 for kind, _ in backend.calls if kind == "submit"),
                )

    def test_timeout_and_duplicate_while_in_progress_do_not_duplicate_work(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backend = SyntheticBackend()
            backend.block = True
            cfg = config(directory, timeout=0.05, grace=0.1)
            with RunningGateway(cfg, settings(directory), backend) as service:
                headers = {
                    "Content-Type": "application/json",
                    "Idempotency-Key": "slow-submit",
                }
                status, _, payload = service.request(
                    "/v1/runs",
                    method="POST",
                    payload=task(),
                    headers=headers,
                )
                self.assertEqual(202, status)
                self.assertEqual("in_progress", payload["data"]["status"])
                status, _, payload = service.request(
                    "/v1/runs",
                    method="POST",
                    payload=task(),
                    headers=headers,
                )
                self.assertEqual(202, status)
                self.assertEqual(
                    1,
                    sum(1 for kind, _ in backend.calls if kind == "submit"),
                )
                backend.release.set()
                deadline = time.monotonic() + 1
                while time.monotonic() < deadline:
                    status, response_headers, payload = service.request(
                        "/v1/runs",
                        method="POST",
                        payload=task(),
                        headers=headers,
                    )
                    if status == 200:
                        break
                    time.sleep(0.01)
                self.assertEqual(200, status)
                self.assertEqual("true", response_headers.get("Idempotency-Replayed"))
                self.assertEqual(
                    1,
                    sum(1 for kind, _ in backend.calls if kind == "submit"),
                )

    def test_unknown_backend_failure_becomes_ambiguous_and_is_not_retried(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backend = SyntheticBackend()
            backend.fail = RuntimeError("secret provider text must never escape")
            with RunningGateway(config(directory), settings(directory), backend) as service:
                headers = {
                    "Content-Type": "application/json",
                    "Idempotency-Key": "ambiguous-1",
                }
                status, _, payload = service.request(
                    "/v1/runs",
                    method="POST",
                    payload=task(),
                    headers=headers,
                )
                self.assertEqual(500, status)
                self.assertEqual("operation_outcome_unknown", payload["error"]["code"])
                self.assertTrue(payload["error"]["recovery_required"])
                self.assertNotIn("secret provider", json.dumps(payload))
                status, _, payload = service.request(
                    "/v1/runs",
                    method="POST",
                    payload=task(),
                    headers=headers,
                )
                self.assertEqual(409, status)
                self.assertEqual("operation_outcome_unknown", payload["error"]["code"])
                self.assertEqual(
                    1,
                    sum(1 for kind, _ in backend.calls if kind == "submit"),
                )

    def test_retryable_safe_backend_failure_releases_network_claim(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backend = SyntheticBackend()
            backend.fail = ApiError(
                "dependency_unavailable",
                "dependency unavailable",
                status=503,
                retryable=True,
            )
            with RunningGateway(config(directory), settings(directory), backend) as service:
                headers = {
                    "Content-Type": "application/json",
                    "Idempotency-Key": "retry-safe",
                }
                self.assertEqual(
                    503,
                    service.request(
                        "/v1/runs",
                        method="POST",
                        payload=task(),
                        headers=headers,
                    )[0],
                )
                backend.fail = None
                self.assertEqual(
                    200,
                    service.request(
                        "/v1/runs",
                        method="POST",
                        payload=task(),
                        headers=headers,
                    )[0],
                )
                self.assertEqual(
                    2,
                    sum(1 for kind, _ in backend.calls if kind == "submit"),
                )

    def test_strict_json_content_encoding_depth_and_header_limits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backend = SyntheticBackend()
            cfg_settings = settings(directory, max_request=2048, max_header=1024)
            with RunningGateway(config(directory), cfg_settings, backend) as service:
                duplicate = b'{"task":{},"task":{}}'
                raw = (
                    b"POST /v1/runs HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
                    b"Idempotency-Key: bad-json\r\nContent-Length: "
                    + str(len(duplicate)).encode()
                    + b"\r\n\r\n"
                    + duplicate
                )
                response = service.raw_exchange(raw)
                self.assertTrue(response.startswith(b"HTTP/1.1 400"), response[:128])
                self.assertNotIn(b"Traceback", response)

                status, _, payload = service.request(
                    "/v1/runs",
                    method="POST",
                    payload=task(),
                    headers={
                        "Content-Type": "application/json; charset=utf-16",
                        "Content-Encoding": "gzip",
                        "Idempotency-Key": "encoding",
                    },
                )
                self.assertEqual(415, status)
                self.assertIn(
                    payload["error"]["code"],
                    {"unsupported_content_encoding", "unsupported_content_type"},
                )

                nested: object = "x"
                for _ in range(20):
                    nested = {"x": nested}
                status, _, payload = service.request(
                    "/v1/runs",
                    method="POST",
                    payload={"task": task()["task"], "prior_state": nested},
                    headers={
                        "Content-Type": "application/json",
                        "Idempotency-Key": "deep",
                    },
                )
                self.assertEqual(400, status)
                self.assertEqual("invalid_request", payload["error"]["code"])

                oversized_header = "x" * 1500
                raw = (
                    b"GET /v1/version HTTP/1.1\r\nHost: x\r\nX-Fill: "
                    + oversized_header.encode()
                    + b"\r\n\r\n"
                )
                response = service.raw_exchange(raw)
                self.assertTrue(response.startswith(b"HTTP/1.1 431"), response[:128])

    def test_noncanonical_content_length_and_expect_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backend = SyntheticBackend()
            with RunningGateway(config(directory), settings(directory), backend) as service:
                raw = (
                    b"POST /v1/runs HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
                    b"Idempotency-Key: length\r\nContent-Length: +10\r\n\r\n0123456789"
                )
                response = service.raw_exchange(raw)
                self.assertTrue(response.startswith(b"HTTP/1.1 400"), response[:128])
                self.assertIn(b"Connection: close", response)

                raw = (
                    b"POST /v1/runs HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
                    b"Idempotency-Key: expect\r\nContent-Length: 2\r\nExpect: 100-continue\r\n\r\n{}"
                )
                response = service.raw_exchange(raw)
                self.assertTrue(response.startswith(b"HTTP/1.1 417"), response[:128])
                self.assertNotIn(b"100 Continue", response)

    def test_unauthorized_request_closes_connection_before_unread_body_can_be_reused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            secret = Path(directory) / "service-auth-token"
            secret.write_text("right-token\n", encoding="utf-8")
            backend = SyntheticBackend()
            cfg = config(directory, secrets=directory)
            with RunningGateway(
                cfg,
                settings(directory, auth_mode="bearer"),
                backend,
            ) as service:
                body = json.dumps(task(), separators=(",", ":")).encode()
                raw = (
                    b"POST /v1/runs HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
                    b"Authorization: Bearer wrong\r\nIdempotency-Key: auth\r\n"
                    + f"Content-Length: {len(body)}\r\nConnection: keep-alive\r\n\r\n".encode()
                    + body
                    + b"GET /livez HTTP/1.1\r\nHost: x\r\n\r\n"
                )
                response = service.raw_exchange(raw)
                self.assertTrue(response.startswith(b"HTTP/1.1 401"), response[:128])
                self.assertEqual(1, response.count(b"HTTP/1.1"))
                self.assertIn(b"Connection: close", response)

    def test_token_rotation_preserves_service_principal_run_access(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            secret = Path(directory) / "service-auth-token"
            secret.write_text("first\n", encoding="utf-8")
            backend = SyntheticBackend()
            cfg = config(directory, secrets=directory)
            with RunningGateway(
                cfg,
                settings(directory, auth_mode="bearer"),
                backend,
            ) as service:
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": "Bearer first",
                    "Idempotency-Key": "rotate-submit",
                }
                self.assertEqual(
                    200,
                    service.request(
                        "/v1/runs",
                        method="POST",
                        payload=task(),
                        headers=headers,
                    )[0],
                )
                secret.write_text("second\n", encoding="utf-8")
                status, _, payload = service.request(
                    "/v1/runs/run:gateway-test",
                    headers={"Authorization": "Bearer second"},
                )
                self.assertEqual(200, status)
                self.assertEqual("completed", payload["data"]["status"])

    def test_shutdown_marks_unfinished_network_operation_ambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backend = SyntheticBackend()
            backend.block = True
            cfg = config(directory, timeout=0.03, grace=0.03)
            store = SQLiteIdempotencyStore(Path(directory) / "api.sqlite3")
            service = RunningGateway(
                cfg,
                settings(directory),
                backend,
                store=store,
            )
            service.__enter__()
            try:
                headers = {
                    "Content-Type": "application/json",
                    "Idempotency-Key": "shutdown-1",
                }
                status, _, _ = service.request(
                    "/v1/runs",
                    method="POST",
                    payload=task(),
                    headers=headers,
                )
                self.assertEqual(202, status)
                self.assertTrue(backend.entered.is_set())
                service.close()
                claim = store.claim(
                    subject="anonymous",
                    key="shutdown-1",
                    method="POST",
                    path="/v1/runs",
                    request_fingerprint=canonical_fingerprint(
                        "POST",
                        "/v1/runs",
                        task(),
                    ),
                    run_id="run:gateway-test",
                )
                self.assertEqual("ambiguous", claim.disposition)
            finally:
                backend.release.set()
                service.close()

    def test_client_disconnect_does_not_cancel_accepted_operation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backend = SyntheticBackend()
            backend.block = True
            cfg = config(directory, timeout=0.2, grace=0.2)
            with RunningGateway(cfg, settings(directory), backend) as service:
                payload = json.dumps(task(), separators=(",", ":")).encode()
                raw = (
                    b"POST /v1/runs HTTP/1.1\r\n"
                    + f"Host: {service.host}:{service.port}\r\n".encode()
                    + b"Content-Type: application/json\r\n"
                    + b"Idempotency-Key: lost-response\r\n"
                    + f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode()
                    + payload
                )
                connection = socket.create_connection(
                    (service.host, service.port),
                    timeout=1,
                )
                connection.sendall(raw)
                connection.close()
                self.assertTrue(backend.entered.wait(1.0))
                backend.release.set()
                deadline = time.monotonic() + 1.0
                headers = {
                    "Content-Type": "application/json",
                    "Idempotency-Key": "lost-response",
                }
                while time.monotonic() < deadline:
                    status, response_headers, body = service.request(
                        "/v1/runs",
                        method="POST",
                        payload=task(),
                        headers=headers,
                    )
                    if status == 200:
                        break
                    time.sleep(0.01)
                self.assertEqual(200, status)
                self.assertEqual("true", response_headers.get("Idempotency-Replayed"))
                self.assertEqual(
                    1,
                    sum(1 for kind, _ in backend.calls if kind == "submit"),
                )

    def test_slow_partial_body_hits_request_timeout_and_closes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backend = SyntheticBackend()
            cfg = config(directory, timeout=0.05)
            with RunningGateway(cfg, settings(directory), backend) as service:
                payload = json.dumps(task(), separators=(",", ":")).encode()
                prefix = (
                    b"POST /v1/runs HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
                    b"Idempotency-Key: slow-client\r\n"
                    + f"Content-Length: {len(payload)}\r\n\r\n".encode()
                    + payload[:4]
                )
                with socket.create_connection(
                    (service.host, service.port),
                    timeout=1,
                ) as connection:
                    connection.settimeout(1)
                    connection.sendall(prefix)
                    time.sleep(0.1)
                    response = connection.recv(65536)
                self.assertTrue(response.startswith(b"HTTP/1.1 408"), response[:128])
                self.assertIn(b"Connection: close", response)
                self.assertEqual([], backend.calls)

    def test_backend_readiness_failure_makes_service_unready(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backend = SyntheticBackend()
            backend.ready_value = (False, "synthetic dependency unavailable")
            with RunningGateway(config(directory), settings(directory), backend) as service:
                status, _, payload = service.request("/readyz")
                self.assertEqual(503, status)
                self.assertFalse(payload["ok"])
                self.assertFalse(payload["dependencies"]["backend"]["ok"])
                status, _, payload = service.request(
                    "/v1/runs",
                    method="POST",
                    payload=task(),
                    headers={
                        "Content-Type": "application/json",
                        "Idempotency-Key": "not-ready",
                    },
                )
                self.assertEqual(503, status)
                self.assertEqual("not_ready", payload["error"]["code"])

    def test_cross_subject_idempotency_and_run_ownership_are_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteIdempotencyStore(Path(directory) / "api.sqlite3")
            payload = task()
            fingerprint = canonical_fingerprint("POST", "/v1/runs", payload)
            first = store.claim(
                subject="principal-a",
                key="same",
                method="POST",
                path="/v1/runs",
                request_fingerprint=fingerprint,
                run_id="run:gateway-test",
            )
            second = store.claim(
                subject="principal-b",
                key="same",
                method="POST",
                path="/v1/runs",
                request_fingerprint=fingerprint,
                run_id="run:gateway-test",
            )
            self.assertEqual("new", first.disposition)
            self.assertEqual("new", second.disposition)
            store.bind_run(subject="principal-a", run_id="run:gateway-test")
            self.assertTrue(
                store.subject_owns_run(
                    subject="principal-a",
                    run_id="run:gateway-test",
                )
            )
            self.assertFalse(
                store.subject_owns_run(
                    subject="principal-b",
                    run_id="run:gateway-test",
                )
            )

    def test_parser_level_long_request_line_uses_safe_json_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backend = SyntheticBackend()
            with RunningGateway(config(directory), settings(directory), backend) as service:
                raw = b"GET /" + b"x" * 70000 + b" HTTP/1.1\r\nHost: x\r\n\r\n"
                response = service.raw_exchange(raw)
                self.assertTrue(response.startswith(b"HTTP/1.1 414"), response[:128])
                self.assertIn(b"application/json", response)
                self.assertNotIn(b"<!DOCTYPE HTML>", response)


if __name__ == "__main__":
    unittest.main()
