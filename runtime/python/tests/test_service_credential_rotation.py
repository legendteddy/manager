from __future__ import annotations

import base64
import hashlib
import hmac
import json
import tempfile
import time
import unittest
from pathlib import Path

from manager_runtime.service.auth import ServiceAuthConfig, ServiceAuthenticator

OLD_KEY = b"synthetic-manager-old-jwt-key-material-0001"
NEW_KEY = b"synthetic-manager-new-jwt-key-material-0002"


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def token(key: bytes, *, token_id: str | None = "rotation-token") -> str:
    now = time.time()
    claims: dict[str, object] = {
        "iss": "https://issuer.example",
        "aud": "manager-service",
        "sub": "rotating-client",
        "exp": now + 120,
        "nbf": now - 5,
        "iat": now - 5,
        "scope": "manager.run",
    }
    if token_id is not None:
        claims["jti"] = token_id
    header = {"alg": "HS256", "typ": "JWT"}
    encoded_header = _b64(json.dumps(header, separators=(",", ":")).encode())
    encoded_claims = _b64(json.dumps(claims, separators=(",", ":")).encode())
    signature = hmac.new(
        key,
        f"{encoded_header}.{encoded_claims}".encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{encoded_header}.{encoded_claims}.{_b64(signature)}"


class ServiceCredentialRotationTests(unittest.TestCase):
    def authenticator(self, directory: str) -> ServiceAuthenticator:
        return ServiceAuthenticator(
            directory,
            ServiceAuthConfig(
                mode="jwt_hs256",
                secret_name="service-jwt-key",
                issuer="https://issuer.example",
                audience="manager-service",
                principal_type="api_client",
                required_capability="manager.run",
                clock_skew_seconds=0,
                require_jti=True,
            ),
        )

    def test_key_rotation_invalidates_old_token_without_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            secret = Path(directory, "service-jwt-key")
            secret.write_bytes(OLD_KEY)
            authenticator = self.authenticator(directory)

            old_token = token(OLD_KEY, token_id="old")
            self.assertIsNotNone(authenticator.authenticate([f"Bearer {old_token}"]))

            secret.write_bytes(NEW_KEY)
            self.assertIsNone(authenticator.authenticate([f"Bearer {old_token}"]))
            new_token = token(NEW_KEY, token_id="new")
            identity = authenticator.authenticate([f"Bearer {new_token}"])
            self.assertIsNotNone(identity)
            assert identity is not None
            self.assertEqual("rotating-client", identity["subject"])

    def test_default_service_identity_requires_token_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "service-jwt-key").write_bytes(OLD_KEY)
            authenticator = self.authenticator(directory)
            self.assertIsNone(
                authenticator.authenticate([f"Bearer {token(OLD_KEY, token_id=None)}"])
            )


if __name__ == "__main__":
    unittest.main()
