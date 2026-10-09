from __future__ import annotations

import json
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from manager_runtime.capacity import CapacityLimits
from manager_runtime.deployment.config import DeploymentConfig
from manager_runtime.observability import InMemoryTelemetrySink
from manager_runtime.operations import OperationalRuntime
from manager_runtime.service.server import (
    ManagerServiceSettings,
    ServiceConfigError,
    create_service_server,
    load_service_settings,
)


def config(*, environment: str = "testing", secrets_dir: str | None = None) -> DeploymentConfig:
    return DeploymentConfig(
        environment=environment,
        bind_host="127.0.0.1",
        bind_port=0,
        state_backend="memory" if environment == "testing" else "sqlite",
        sqlite_path=None if environment == "testing" else "/tmp/manager-service-test.sqlite3",
        instance_count=1,
        max_concurrency=2,
        queue_limit=2,
        request_timeout_seconds=5.0,
        provider_timeout_seconds=3.0,
        mcp_timeout_seconds=3.0,
        graceful_shutdown_seconds=2.0,
        tls_mode="off" if environment == "testing" else "external",
        tls_cert_file=None,
        tls_key_file=None,
        telemetry_mode="off" if environment == "testing" else "json_stdout",
        data_dir="/tmp",
        tmp_dir="/tmp",
        secrets_dir=secrets_dir,
        read_only_root=environment != "testing",
    )


def task() -> dict:
    return {
        "task": {
            "task_id": "service-test",
            "objective": "Summarize the synthetic input.",
            "classification": {
                "materiality": "routine",
                "consequence": "low",
                "uncertainty": "low",
                "reversibility": "reversible",
                "sensitivity": "public",
            },
        }
    }


class RunningService:
    def __init__(
        self,
        cfg: DeploymentConfig,
        settings: ManagerServiceSettings,
        *,
        operations: OperationalRuntime | None = None,
    ) -> None:
        self.server, self.context = create_service_server(
            cfg,
            settings,
            operations=operations,
        )
        host, port = self.server.server_address[:2]
        self.host = str(host)
        self.port = int(port)
        self.base = f"http://{host}:{port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> "RunningService":
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.context.health.begin_shutdown()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict]:
        request = urllib.request.Request(
            self.base + path,
            data=body,
            method=method,
            headers=headers or {},
        )
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def raw_exchange(self, raw: bytes) -> bytes:
        chunks: list[bytes] = []
        with socket.create_connection((self.host, self.port), timeout=2) as connection:
            connection.settimeout(2)
            connection.sendall(raw)
            while True:
                try:
                    chunk = connection.recv(65536)
                except socket.timeout:
                    break
                if not chunk:
                    break
                chunks.append(chunk)
        return b"".join(chunks)


class ServiceRuntimeTests(unittest.TestCase):
    def test_production_cannot_disable_authentication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cfg = config(environment="production", secrets_dir=directory)
            with self.assertRaisesRegex(ServiceConfigError, "cannot be disabled"):
                load_service_settings(
                    cfg,
                    environ={"MANAGER_SERVICE_AUTH_MODE": "none"},
                )

    def test_health_and_routine_control_plane_request(self) -> None:
        settings = ManagerServiceSettings("none", "service-auth-token", 1024 * 1024)
        with RunningService(config(), settings) as service:
            status, payload = service.request("/livez")
            self.assertEqual(200, status)
            self.assertTrue(payload["ok"])
            status, payload = service.request("/readyz")
            self.assertEqual(200, status)
            self.assertTrue(payload["ok"])
            self.assertTrue(payload["dependencies"]["operations"]["ok"])
            raw = json.dumps(task()).encode()
            status, payload = service.request(
                "/v1/run",
                method="POST",
                body=raw,
                headers={"Content-Type": "application/json"},
            )
            self.assertEqual(200, status)
            self.assertTrue(payload["ok"])
            self.assertEqual("completed", payload["output"]["trace"]["status"])

    def test_material_request_remains_blocked_by_governance(self) -> None:
        payload = task()
        payload["task"]["classification"]["materiality"] = "material"
        settings = ManagerServiceSettings("none", "service-auth-token", 1024 * 1024)
        with RunningService(config(), settings) as service:
            status, response = service.request(
                "/v1/run",
                method="POST",
                body=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
            )
            self.assertEqual(200, status)
            self.assertEqual("blocked", response["output"]["trace"]["status"])
            self.assertEqual("pending", response["output"]["approval"]["status"])

    def test_bearer_secret_is_required_and_rotatable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            secret = Path(directory) / "service-auth-token"
            secret.write_text("first-token\n", encoding="utf-8")
            cfg = config(secrets_dir=directory)
            settings = ManagerServiceSettings(
                "bearer",
                "service-auth-token",
                1024 * 1024,
            )
            with RunningService(cfg, settings) as service:
                raw = json.dumps(task()).encode()
                status, _ = service.request(
                    "/v1/run",
                    method="POST",
                    body=raw,
                    headers={"Content-Type": "application/json"},
                )
                self.assertEqual(401, status)
                status, response = service.request(
                    "/v1/run",
                    method="POST",
                    body=raw,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": "Bearer first-token",
                    },
                )
                self.assertEqual(200, status)
                self.assertTrue(response["ok"])
                secret.write_text("second-token\n", encoding="utf-8")
                status, _ = service.request(
                    "/v1/run",
                    method="POST",
                    body=raw,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": "Bearer first-token",
                    },
                )
                self.assertEqual(401, status)
                status, _ = service.request(
                    "/v1/run",
                    method="POST",
                    body=raw,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": "Bearer second-token",
                    },
                )
                self.assertEqual(200, status)

    def test_duplicate_json_nonfinite_unknown_and_oversized_requests_fail_closed(self) -> None:
        settings = ManagerServiceSettings("none", "service-auth-token", 1024)
        with RunningService(config(), settings) as service:
            cases = [
                b'{"task":{},"task":{}}',
                b'{"task":{"task_id":"x","objective":"x","classification":{"materiality":"routine","consequence":"low","uncertainty":NaN}}}',
                b'{"task":{"task_id":"x","objective":"x","classification":{"materiality":"routine","consequence":"low","uncertainty":"low"}},"surprise":true}',
            ]
            for raw in cases:
                with self.subTest(raw=raw[:32]):
                    status, response = service.request(
                        "/v1/run",
                        method="POST",
                        body=raw,
                        headers={"Content-Type": "application/json"},
                    )
                    self.assertEqual(400, status)
                    self.assertEqual("invalid_request", response["error"])
            status, response = service.request(
                "/v1/run",
                method="POST",
                body=b"x" * 2048,
                headers={"Content-Type": "application/json"},
            )
            self.assertEqual(413, status)
            self.assertEqual("request_too_large", response["error"])

    def test_conflicting_duplicate_content_length_is_rejected_and_connection_closed(self) -> None:
        settings = ManagerServiceSettings("none", "service-auth-token", 1024 * 1024)
        body = json.dumps(task(), separators=(",", ":")).encode()
        with RunningService(config(), settings) as service:
            raw = (
                b"POST /v1/run HTTP/1.1\r\n"
                + f"Host: {service.host}:{service.port}\r\n".encode()
                + b"Content-Type: application/json\r\n"
                + f"Content-Length: {len(body)}\r\n".encode()
                + b"Content-Length: 0\r\n"
                + b"Connection: keep-alive\r\n\r\n"
                + body
                + b"GET /livez HTTP/1.1\r\nHost: synthetic\r\n\r\n"
            )
            response = service.raw_exchange(raw)
            self.assertTrue(response.startswith(b"HTTP/1.1 400"), response[:128])
            self.assertEqual(1, response.count(b"HTTP/1.1"))
            self.assertIn(b"Connection: close", response)

    def test_noncanonical_signed_content_length_is_rejected(self) -> None:
        settings = ManagerServiceSettings("none", "service-auth-token", 1024 * 1024)
        body = json.dumps(task(), separators=(",", ":")).encode()
        with RunningService(config(), settings) as service:
            raw = (
                b"POST /v1/run HTTP/1.1\r\n"
                + f"Host: {service.host}:{service.port}\r\n".encode()
                + b"Content-Type: application/json\r\n"
                + f"Content-Length: +{len(body)}\r\n".encode()
                + b"Connection: keep-alive\r\n\r\n"
                + body
            )
            response = service.raw_exchange(raw)
            self.assertTrue(response.startswith(b"HTTP/1.1 400"), response[:128])
            self.assertIn(b"Connection: close", response)

    def test_run_saturation_uses_shared_operational_runtime(self) -> None:
        settings = ManagerServiceSettings("none", "service-auth-token", 1024 * 1024)
        cfg = replace(config(), max_concurrency=1, queue_limit=1)
        sink = InMemoryTelemetrySink(max_records=256)
        operations = OperationalRuntime(
            limits=CapacityLimits(active_runs=1, queued_runs=1),
            telemetry_sink=sink,
        )
        entered = threading.Event()
        release = threading.Event()
        first_finished = threading.Event()

        def blocked_control_plane(_task_input: dict) -> dict:
            entered.set()
            release.wait(2.0)
            return {"trace": {"status": "completed"}}

        with patch(
            "manager_runtime.service.server.run_control_plane",
            side_effect=blocked_control_plane,
        ):
            with RunningService(cfg, settings, operations=operations) as service:
                raw = json.dumps(task()).encode()

                def issue_first() -> None:
                    try:
                        service.request(
                            "/v1/run",
                            method="POST",
                            body=raw,
                            headers={"Content-Type": "application/json"},
                        )
                    finally:
                        first_finished.set()

                first = threading.Thread(target=issue_first, daemon=True)
                first.start()
                self.assertTrue(entered.wait(1.0))

                status, response = service.request(
                    "/v1/run",
                    method="POST",
                    body=raw,
                    headers={"Content-Type": "application/json"},
                )
                self.assertEqual(503, status)
                self.assertEqual("service_overloaded", response["error"])

                status, ready = service.request("/readyz")
                self.assertEqual(200, status)
                self.assertTrue(ready["ok"])
                self.assertFalse(ready["dependencies"]["operations"]["ok"])
                self.assertEqual(
                    "status=saturated",
                    ready["dependencies"]["operations"]["detail"],
                )

                records = sink.snapshot()["records"]
                self.assertTrue(
                    any(
                        kind == "metric"
                        and value.name == "manager_overload_rejections_total"
                        for kind, value in records
                    )
                )
                self.assertTrue(
                    any(
                        kind == "event" and value.name == "overload.rejected"
                        for kind, value in records
                    )
                )

                release.set()
                self.assertTrue(first_finished.wait(1.0))

    def test_shutdown_budget_bounds_uncooperative_inflight_request(self) -> None:
        settings = ManagerServiceSettings("none", "service-auth-token", 1024 * 1024)
        cfg = replace(config(), graceful_shutdown_seconds=0.05)
        entered = threading.Event()
        release = threading.Event()
        request_finished = threading.Event()

        def blocked_control_plane(_task_input: dict) -> dict:
            entered.set()
            release.wait(2.0)
            return {"trace": {"status": "completed"}}

        with patch(
            "manager_runtime.service.server.run_control_plane",
            side_effect=blocked_control_plane,
        ):
            with RunningService(cfg, settings) as service:
                raw = json.dumps(task()).encode()

                def issue_request() -> None:
                    try:
                        service.request(
                            "/v1/run",
                            method="POST",
                            body=raw,
                            headers={"Content-Type": "application/json"},
                        )
                    except Exception:
                        pass
                    finally:
                        request_finished.set()

                request_thread = threading.Thread(target=issue_request, daemon=True)
                request_thread.start()
                self.assertTrue(entered.wait(1.0))
                service.context.health.begin_shutdown()
                service.server.shutdown()
                started = time.monotonic()
                service.server.server_close()
                elapsed = time.monotonic() - started
                self.assertLess(elapsed, 0.5)
                release.set()
                self.assertTrue(request_finished.wait(1.0))

    def test_shutdown_drops_readiness_before_liveness(self) -> None:
        settings = ManagerServiceSettings("none", "service-auth-token", 1024 * 1024)
        with RunningService(config(), settings) as service:
            service.context.health.begin_shutdown()
            status, ready = service.request("/readyz")
            self.assertEqual(503, status)
            self.assertEqual("draining", ready["status"])
            status, live = service.request("/livez")
            self.assertEqual(200, status)
            self.assertTrue(live["ok"])


if __name__ == "__main__":
    unittest.main()
