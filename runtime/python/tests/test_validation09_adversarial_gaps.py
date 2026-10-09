from __future__ import annotations

import json
import socket
import threading
import unittest

from manager_runtime.deployment.config import DeploymentConfig
from manager_runtime.serialization import bounded_json_text
from manager_runtime.service.server import ManagerServiceSettings, create_service_server


def _config() -> DeploymentConfig:
    return DeploymentConfig(
        environment="testing",
        bind_host="127.0.0.1",
        bind_port=0,
        state_backend="memory",
        sqlite_path=None,
        instance_count=1,
        max_concurrency=2,
        queue_limit=2,
        request_timeout_seconds=2.0,
        provider_timeout_seconds=1.0,
        mcp_timeout_seconds=1.0,
        graceful_shutdown_seconds=1.0,
        tls_mode="off",
        tls_cert_file=None,
        tls_key_file=None,
        telemetry_mode="off",
        data_dir="/tmp",
        tmp_dir="/tmp",
        secrets_dir=None,
        read_only_root=False,
    )


def _task_body() -> bytes:
    return json.dumps(
        {
            "task": {
                "task_id": "validation09-http-framing",
                "objective": "Exercise strict HTTP framing with synthetic input.",
                "classification": {
                    "materiality": "routine",
                    "consequence": "low",
                    "uncertainty": "low",
                    "reversibility": "reversible",
                    "sensitivity": "public",
                },
            }
        },
        separators=(",", ":"),
    ).encode("utf-8")


class RunningService:
    def __init__(self) -> None:
        settings = ManagerServiceSettings("none", "service-auth-token", 1024 * 1024)
        self.server, self.context = create_service_server(_config(), settings)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> "RunningService":
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.context.health.begin_shutdown()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def raw_request(self, request: bytes) -> bytes:
        host, port = self.server.server_address[:2]
        chunks: list[bytes] = []
        with socket.create_connection((host, port), timeout=2) as connection:
            connection.settimeout(2)
            connection.sendall(request)
            connection.shutdown(socket.SHUT_WR)
            while True:
                try:
                    chunk = connection.recv(65536)
                except socket.timeout:
                    break
                if not chunk:
                    break
                chunks.append(chunk)
        return b"".join(chunks)


class HTTPFramingAdversarialTests(unittest.TestCase):
    def test_conflicting_duplicate_content_length_is_rejected(self) -> None:
        """Conflicting framing must fail closed instead of selecting one value."""
        body = _task_body()
        request = (
            b"POST /v1/run HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            b"Content-Type: application/json\r\n"
            + f"Content-Length: {len(body)}\r\n".encode("ascii")
            + b"Content-Length: 0\r\n"
            b"Connection: close\r\n"
            b"\r\n"
            + body
        )
        with RunningService() as service:
            response = service.raw_request(request)
        self.assertTrue(
            response.startswith(b"HTTP/1.1 400 "),
            msg=f"conflicting Content-Length was not rejected: {response[:160]!r}",
        )

    def test_noncanonical_signed_content_length_is_rejected(self) -> None:
        """HTTP Content-Length grammar is digits only; parser differentials fail closed."""
        body = _task_body()
        request = (
            b"POST /v1/run HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            b"Content-Type: application/json\r\n"
            + f"Content-Length: +{len(body)}\r\n".encode("ascii")
            + b"Connection: close\r\n"
            b"\r\n"
            + body
        )
        with RunningService() as service:
            response = service.raw_request(request)
        self.assertTrue(
            response.startswith(b"HTTP/1.1 400 "),
            msg=f"noncanonical Content-Length was accepted: {response[:160]!r}",
        )


class _ExplosiveDict(dict):
    def items(self):  # pragma: no cover - this hook must never execute
        raise AssertionError("bounded serialization executed hostile dict.items")


class _ExplosiveList(list):
    def __iter__(self):  # pragma: no cover - this hook must never execute
        raise AssertionError("bounded serialization executed hostile list.__iter__")


class ToolResultSerializationAdversarialTests(unittest.TestCase):
    def test_bounded_serialization_does_not_execute_collection_subclass_hooks(self) -> None:
        """Untrusted tool outputs must not gain code execution through container hooks."""
        hostile_values = [
            _ExplosiveDict({"safe": "value"}),
            _ExplosiveList(["safe", "value"]),
        ]
        for value in hostile_values:
            with self.subTest(value_type=type(value).__name__):
                try:
                    rendered = bounded_json_text(value, 128)
                except AssertionError as exc:
                    self.fail(str(exc))
                self.assertLessEqual(len(rendered), 128)


if __name__ == "__main__":
    unittest.main()
