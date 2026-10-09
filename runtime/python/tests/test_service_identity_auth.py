from __future__ import annotations

import base64
import hashlib
import hmac
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
from manager_runtime.service.server import (
    ManagerServiceSettings,
    ServiceConfigError,
    create_service_server,
    load_service_settings,
)

SYNTHETIC_KEY = b"synthetic-manager-service-jwt-key-32-bytes!!"


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def jwt_token(
    *,
    issuer: str = "https://issuer.example",
    audience: str = "manager-service",
    subject: str = "synthetic-api-client",
    expires_at: float | None = None,
    token_id: str = "synthetic-token-1",
    scope: str = "manager.run",
) -> str:
    now = time.time()
    claims = {
        "iss": issuer,
        "aud": audience,
        "sub": subject,
        "exp": expires_at if expires_at is not None else now + 120,
        "nbf": now - 5,
        "iat": now - 5,
        "jti": token_id,
        "scope": scope,
    }
    header = {"alg": "HS256", "typ": "JWT"}
    a = _b64(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    b = _b64(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(
        SYNTHETIC_KEY,
        f"{a}.{b}".encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{a}.{b}.{_b64(signature)}"


def config(*, environment: str = "testing", secrets_dir: str | None = None) -> DeploymentConfig:
    return DeploymentConfig(
        environment=environment,
        bind_host="127.0.0.1",
        bind_port=0,
        state_backend="memory" if environment == "testing" else "sqlite",
        sqlite_path=None if environment == "testing" else "/tmp/manager-service-identity-test.sqlite3",
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
        telemetry_mode="off",
        data_dir="/tmp",
        tmp_dir="/tmp",
        secrets_dir=secrets_dir,
        read_only_root=environment != "testing",
    )


def task(*, material: bool = False) -> dict:
    return {
        "task": {
            "task_id": "service-identity-test",
            "objective": "Summarize the synthetic input.",
            "classification": {
                "materiality": "material" if material else "routine",
                "consequence": "low",
                "uncertainty": "low",
                "reversibility": "reversible",
                "sensitivity": "public",
            },
        }
    }


class RunningService:
    def __init__(self, cfg: DeploymentConfig, settings: ManagerServiceSettings) -> None:
        self.server, self.context = create_service_server(cfg, settings)
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

    def request(self, token: str, *, material: bool = False) -> tuple[int, dict]:
        raw = json.dumps(task(material=material)).encode("utf-8")
        request = urllib.request.Request(
            self.base + "/v1/run",
            data=raw,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
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


class ServiceIdentityAuthenticationTests(unittest.TestCase):
    def settings(self) -> ManagerServiceSettings:
        return ManagerServiceSettings(
            "jwt_hs256",
            "service-jwt-key",
            1024 * 1024,
            auth_issuer="https://issuer.example",
            auth_audience="manager-service",
            auth_principal_type="api_client",
            auth_clock_skew_seconds=0,
            auth_require_jti=True,
        )

    def test_production_accepts_explicit_jwt_identity_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            loaded = load_service_settings(
                config(environment="production", secrets_dir=directory),
                environ={
                    "MANAGER_SERVICE_AUTH_MODE": "jwt_hs256",
                    "MANAGER_SERVICE_AUTH_SECRET_NAME": "service-jwt-key",
                    "MANAGER_SERVICE_AUTH_ISSUER": "https://issuer.example",
                    "MANAGER_SERVICE_AUTH_AUDIENCE": "manager-service",
                },
            )
        self.assertEqual("jwt_hs256", loaded.auth_mode)
        self.assertEqual("https://issuer.example", loaded.auth_issuer)
        self.assertEqual("manager-service", loaded.auth_audience)

    def test_jwt_mode_requires_issuer_and_audience(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cfg = config(environment="production", secrets_dir=directory)
            with self.assertRaisesRegex(ServiceConfigError, "AUTH_ISSUER"):
                load_service_settings(
                    cfg,
                    environ={"MANAGER_SERVICE_AUTH_MODE": "jwt_hs256"},
                )
            with self.assertRaisesRegex(ServiceConfigError, "AUTH_AUDIENCE"):
                load_service_settings(
                    cfg,
                    environ={
                        "MANAGER_SERVICE_AUTH_MODE": "jwt_hs256",
                        "MANAGER_SERVICE_AUTH_ISSUER": "https://issuer.example",
                    },
                )

    def test_valid_jwt_establishes_explicit_api_caller_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "service-jwt-key").write_bytes(SYNTHETIC_KEY)
            with RunningService(config(secrets_dir=directory), self.settings()) as service:
                token = jwt_token(subject="client-42")
                identity = service.context.authenticator.authenticate([f"Bearer {token}"])
                self.assertIsNotNone(identity)
                assert identity is not None
                self.assertTrue(identity["authenticated"])
                self.assertTrue(identity["authorized"])
                self.assertEqual("client-42", identity["subject"])
                self.assertEqual("api_client", identity["principal_type"])
                self.assertEqual("jwt_hs256", identity["authentication_method"])
                status, response = service.request(token)
                self.assertEqual(200, status)
                self.assertTrue(response["ok"])

    def test_valid_identity_without_run_capability_is_unauthorized(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "service-jwt-key").write_bytes(SYNTHETIC_KEY)
            with RunningService(config(secrets_dir=directory), self.settings()) as service:
                token = jwt_token(scope="manager.read")
                self.assertIsNone(
                    service.context.authenticator.authenticate([f"Bearer {token}"])
                )
                status, response = service.request(token)
                self.assertEqual(401, status)
                self.assertEqual("unauthorized", response["error"])

    def test_wrong_issuer_audience_expiry_and_signature_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "service-jwt-key").write_bytes(SYNTHETIC_KEY)
            with RunningService(config(secrets_dir=directory), self.settings()) as service:
                candidates = [
                    jwt_token(issuer="https://attacker.example"),
                    jwt_token(audience="other-service"),
                    jwt_token(expires_at=time.time() - 10),
                ]
                valid = jwt_token()
                head, body, signature = valid.split(".")
                candidates.append(f"{head}.{body}.{signature[:-1]}A")
                for candidate in candidates:
                    with self.subTest(token=candidate[-16:]):
                        status, response = service.request(candidate)
                        self.assertEqual(401, status)
                        self.assertEqual("unauthorized", response["error"])

    def test_duplicate_authorization_headers_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "service-jwt-key").write_bytes(SYNTHETIC_KEY)
            with RunningService(config(secrets_dir=directory), self.settings()) as service:
                token = jwt_token()
                body = json.dumps(task(), separators=(",", ":")).encode("utf-8")
                raw = (
                    b"POST /v1/run HTTP/1.1\r\n"
                    + f"Host: {service.host}:{service.port}\r\n".encode("ascii")
                    + b"Content-Type: application/json\r\n"
                    + f"Authorization: Bearer {token}\r\n".encode("ascii")
                    + b"Authorization: Bearer attacker-controlled-second-value\r\n"
                    + f"Content-Length: {len(body)}\r\n".encode("ascii")
                    + b"Connection: close\r\n\r\n"
                    + body
                )
                response = service.raw_exchange(raw)
                self.assertTrue(response.startswith(b"HTTP/1.1 401"), response[:128])
                self.assertIn(b'"error":"unauthorized"', response)

    def test_authentication_does_not_turn_into_material_authorization(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "service-jwt-key").write_bytes(SYNTHETIC_KEY)
            with RunningService(config(secrets_dir=directory), self.settings()) as service:
                status, response = service.request(jwt_token(), material=True)
                self.assertEqual(200, status)
                self.assertEqual("blocked", response["output"]["trace"]["status"])
                self.assertEqual("pending", response["output"]["approval"]["status"])


if __name__ == "__main__":
    unittest.main()
