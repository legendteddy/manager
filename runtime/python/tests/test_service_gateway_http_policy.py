from __future__ import annotations

import json
import socket
import tempfile
import threading
import unittest
from pathlib import Path

from manager_runtime.deployment.config import DeploymentConfig
from manager_runtime.service.api import JsonLimits
from manager_runtime.service.gateway import GatewaySettings, create_gateway_server


class _Backend:
    def readiness(self):
        return True

    def submit(self, task_input, *, subject):
        raise AssertionError("not used")

    def get_run(self, run_id, *, subject):
        raise AssertionError("not used")

    def resume(self, run_id, *, subject):
        raise AssertionError("not used")

    def cancel(self, run_id, *, subject):
        raise AssertionError("not used")

    def decide_approval(self, run_id, decision, *, subject):
        raise AssertionError("not used")

    def resolve_recovery(self, run_id, resolution, *, subject):
        raise AssertionError("not used")


def _config(directory: str, *, secrets: str | None = None) -> DeploymentConfig:
    return DeploymentConfig(
        environment="testing",
        bind_host="127.0.0.1",
        bind_port=0,
        state_backend="memory",
        sqlite_path=None,
        instance_count=1,
        max_concurrency=1,
        queue_limit=1,
        request_timeout_seconds=0.5,
        provider_timeout_seconds=0.5,
        mcp_timeout_seconds=0.5,
        graceful_shutdown_seconds=0.2,
        tls_mode="off",
        tls_cert_file=None,
        tls_key_file=None,
        telemetry_mode="off",
        data_dir=directory,
        tmp_dir=directory,
        secrets_dir=secrets,
        read_only_root=False,
    )


def _settings(directory: str, *, auth_mode: str = "none") -> GatewaySettings:
    return GatewaySettings(
        auth_mode=auth_mode,
        auth_secret_name="service-auth-token",
        backend_factory=None,
        idempotency_path=str(Path(directory) / "api.sqlite3"),
        max_request_bytes=4096,
        max_header_bytes=4096,
        json_limits=JsonLimits(max_depth=8, max_nodes=512, max_string_chars=4096),
    )


class _Running:
    def __init__(self, directory: str, *, auth_mode: str = "none") -> None:
        self.server, self.context = create_gateway_server(
            _config(directory, secrets=directory if auth_mode == "bearer" else None),
            _settings(directory, auth_mode=auth_mode),
            _Backend(),
        )
        self.host, self.port = self.server.server_address[:2]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.context.health.begin_shutdown()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)

    def exchange(self, raw: bytes) -> bytes:
        chunks = []
        with socket.create_connection((self.host, self.port), timeout=1) as connection:
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


class GatewayHTTPPolicyTests(unittest.TestCase):
    def test_get_body_cannot_be_reinterpreted_as_pipelined_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with _Running(directory) as service:
                smuggled = b"GET /livez HTTP/1.1\r\nHost: x\r\n\r\n"
                raw = (
                    b"GET /livez HTTP/1.1\r\nHost: x\r\nConnection: keep-alive\r\n"
                    + f"Content-Length: {len(smuggled)}\r\n\r\n".encode()
                    + smuggled
                )
                response = service.exchange(raw)
                self.assertTrue(response.startswith(b"HTTP/1.1 400"), response[:128])
                self.assertEqual(1, response.count(b"HTTP/1.1"))
                self.assertIn(b"Connection: close", response)

    def test_unsupported_method_closes_before_unread_body_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with _Running(directory) as service:
                body = b"GET /livez HTTP/1.1\r\nHost: x\r\n\r\n"
                raw = (
                    b"PUT /v1/runs HTTP/1.1\r\nHost: x\r\nConnection: keep-alive\r\n"
                    + f"Content-Length: {len(body)}\r\n\r\n".encode()
                    + body
                )
                response = service.exchange(raw)
                self.assertTrue(response.startswith(b"HTTP/1.1 405"), response[:128])
                self.assertEqual(1, response.count(b"HTTP/1.1"))
                self.assertIn(b"Connection: close", response)

    def test_duplicate_authorization_headers_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "service-auth-token").write_text("good\n", encoding="utf-8")
            with _Running(directory, auth_mode="bearer") as service:
                raw = (
                    b"GET /v1/version HTTP/1.1\r\nHost: x\r\n"
                    b"Authorization: Bearer good\r\n"
                    b"Authorization: Bearer attacker\r\n\r\n"
                )
                response = service.exchange(raw)
                self.assertTrue(response.startswith(b"HTTP/1.1 401"), response[:128])
                payload = response.split(b"\r\n\r\n", 1)[1]
                self.assertEqual("unauthorized", json.loads(payload)["error"]["code"])


if __name__ == "__main__":
    unittest.main()
