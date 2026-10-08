from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import pickle
import tempfile
import unittest
from pathlib import Path

from manager_runtime.security import (
    EnvironmentSecretProvider,
    HS256JWTValidator,
    JWTValidationConfig,
    MountedFileSecretProvider,
    NetworkSecurityPolicy,
    SecretLease,
    SecurityBoundaryError,
    evaluate_tool_authorization,
    redact_sensitive_text,
    safe_error,
)

NOW = 2_000_000_000.0


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def token(secret: bytes, claims: dict, *, alg: str = "HS256") -> str:
    header = {"alg": alg, "typ": "JWT", "kid": "primary"}
    a = b64(json.dumps(header, separators=(",", ":")).encode())
    b = b64(json.dumps(claims, separators=(",", ":")).encode())
    signature = hmac.new(secret, f"{a}.{b}".encode("ascii"), hashlib.sha256).digest()
    return f"{a}.{b}.{b64(signature)}"


class DictProvider:
    def __init__(self, values: dict[str, bytes]) -> None:
        self.values = values
        self.calls = 0

    def acquire(self, name: str) -> SecretLease:
        self.calls += 1
        if name not in self.values:
            raise SecurityBoundaryError("credential_unavailable")
        return SecretLease(self.values[name])


class Revocations:
    def __init__(self, revoked: set[str]) -> None:
        self.revoked = revoked

    def is_revoked(self, identity: dict) -> bool:
        return identity.get("token_id") in self.revoked


class SecurityTests(unittest.TestCase):
    def claims(self, **overrides):
        result = {
            "iss": "https://issuer.example",
            "aud": "manager-api",
            "sub": "worker-17",
            "exp": NOW + 300,
            "nbf": NOW - 5,
            "iat": NOW - 30,
            "jti": "token-1",
            "scope": "tools.read",
            "capabilities": ["project.view"],
        }
        result.update(overrides)
        return result

    def validator(self, provider=None, revocations=None):
        provider = provider or DictProvider({"jwt-primary": b"synthetic-secret"})
        return HS256JWTValidator(
            JWTValidationConfig(
                issuers=frozenset({"https://issuer.example"}),
                audiences=frozenset({"manager-api"}),
                clock_skew_seconds=0,
                require_jti=True,
            ),
            provider,
            secret_name=lambda kid: "jwt-primary" if kid == "primary" else "missing",
            revocation_checker=revocations,
            clock=lambda: NOW,
        )

    def test_valid_token_authenticates_without_granting_authorization(self):
        identity = self.validator().validate(
            token(b"synthetic-secret", self.claims()), principal_type="worker"
        )
        self.assertEqual("worker-17", identity["subject"])
        self.assertEqual(["project.view", "tools.read"], identity["capabilities"])
        decision = evaluate_tool_authorization(
            {
                "security_context": {"principal": identity, "environment": "prod"},
                "security_policy": {"revision": "r1", "rules": []},
            },
            {"tool_name": "read_project", "target": "project:7"},
            {"side_effect_class": "read"},
            now=NOW,
        )
        self.assertFalse(decision["allowed"])

    def test_expired_identity_rejected(self):
        jwt = token(b"synthetic-secret", self.claims(exp=NOW))
        with self.assertRaisesRegex(SecurityBoundaryError, "identity_expired"):
            self.validator().validate(jwt)

    def test_invalid_audience_rejected(self):
        jwt = token(b"synthetic-secret", self.claims(aud="other-service"))
        with self.assertRaisesRegex(SecurityBoundaryError, "identity_audience_rejected"):
            self.validator().validate(jwt)

    def test_invalid_issuer_rejected(self):
        jwt = token(b"synthetic-secret", self.claims(iss="https://evil.example"))
        with self.assertRaisesRegex(SecurityBoundaryError, "identity_issuer_rejected"):
            self.validator().validate(jwt)

    def test_wrong_service_identity_denied_by_principal_type(self):
        identity = self.validator().validate(
            token(b"synthetic-secret", self.claims()), principal_type="model_provider"
        )
        decision = evaluate_tool_authorization(
            {
                "security_context": {"principal": identity, "environment": "prod"},
                "security_policy": {
                    "revision": "r1",
                    "allowed_principal_types": ["worker"],
                    "rules": [
                        {
                            "capability": "tools.read",
                            "actions": ["tool:read_project"],
                            "resources": ["project:*"],
                            "environments": ["prod"],
                            "side_effect_classes": ["read"],
                        }
                    ],
                },
            },
            {"tool_name": "read_project", "target": "project:7"},
            {"side_effect_class": "read"},
            now=NOW,
        )
        self.assertFalse(decision["allowed"])
        self.assertEqual("principal_type_not_allowed", decision["reason"])

    def test_forged_capability_does_not_match_policy(self):
        identity = self.validator().validate(
            token(b"synthetic-secret", self.claims(scope="tools.fake")),
            principal_type="worker",
        )
        decision = evaluate_tool_authorization(
            {
                "security_context": {"principal": identity, "environment": "prod"},
                "security_policy": {
                    "revision": "r1",
                    "rules": [
                        {
                            "capability": "tools.write",
                            "actions": ["tool:update_project"],
                            "resources": ["project:*"],
                            "environments": ["prod"],
                            "side_effect_classes": ["reversible_write"],
                        }
                    ],
                },
            },
            {"tool_name": "update_project", "target": "project:7"},
            {"side_effect_class": "reversible_write"},
            now=NOW,
        )
        self.assertFalse(decision["allowed"])

    def test_tampered_capability_signature_rejected(self):
        original = token(b"synthetic-secret", self.claims(scope="tools.read"))
        head, _, signature = original.split(".")
        claims = self.claims(scope="tools.write")
        tampered_body = b64(json.dumps(claims, separators=(",", ":")).encode())
        tampered = f"{head}.{tampered_body}.{signature}"
        with self.assertRaisesRegex(SecurityBoundaryError, "identity_signature_invalid"):
            self.validator().validate(tampered)

    def test_authorization_binding_changes_with_policy_and_principal(self):
        identity = self.validator().validate(
            token(b"synthetic-secret", self.claims(scope="tools.write")),
            principal_type="worker",
        )
        auth = {
            "security_context": {"principal": identity, "environment": "prod"},
            "security_policy": {
                "revision": "r1",
                "rules": [
                    {
                        "capability": "tools.write",
                        "actions": ["tool:update_project"],
                        "resources": ["project:*"],
                        "environments": ["prod"],
                        "side_effect_classes": ["reversible_write"],
                    }
                ],
            },
        }
        request = {"tool_name": "update_project", "target": "project:7"}
        definition = {"side_effect_class": "reversible_write"}
        first = evaluate_tool_authorization(auth, request, definition, now=NOW)
        self.assertTrue(first["allowed"])
        auth["security_policy"] = dict(auth["security_policy"], revision="r2")
        second = evaluate_tool_authorization(auth, request, definition, now=NOW)
        self.assertTrue(second["allowed"])
        self.assertNotEqual(first["binding"], second["binding"])

    def test_revoked_identity_rejected(self):
        jwt = token(b"synthetic-secret", self.claims(jti="revoked"))
        with self.assertRaisesRegex(SecurityBoundaryError, "identity_revoked"):
            self.validator(revocations=Revocations({"revoked"})).validate(jwt)

    def test_unsigned_or_wrong_algorithm_rejected(self):
        jwt = token(b"synthetic-secret", self.claims(), alg="none")
        with self.assertRaisesRegex(SecurityBoundaryError, "identity_algorithm_rejected"):
            self.validator().validate(jwt)

    def test_secret_lease_cannot_be_serialized_or_printed(self):
        lease = SecretLease(b"synthetic-secret")
        self.assertNotIn("synthetic-secret", repr(lease))
        with self.assertRaises(TypeError):
            pickle.dumps(lease)

    def test_environment_provider_rotates_on_each_acquisition(self):
        os.environ["TEST_SECRET"] = "first"
        provider = EnvironmentSecretProvider()
        self.assertEqual(b"first", provider.acquire("TEST_SECRET").reveal(now=NOW))
        os.environ["TEST_SECRET"] = "second"
        self.assertEqual(b"second", provider.acquire("TEST_SECRET").reveal(now=NOW))
        del os.environ["TEST_SECRET"]

    def test_mounted_secret_provider_blocks_path_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "token").write_text("synthetic\n", encoding="utf-8")
            provider = MountedFileSecretProvider(directory)
            self.assertEqual(b"synthetic", provider.acquire("token").reveal(now=NOW))
            with self.assertRaises(ValueError):
                provider.acquire("../token")

    def test_production_tls_bypass_rejected(self):
        with self.assertRaises(ValueError):
            NetworkSecurityPolicy(production=True, verify_tls=False)

    def test_production_rejects_cleartext_http(self):
        policy = NetworkSecurityPolicy(production=True)
        with self.assertRaisesRegex(SecurityBoundaryError, "network_cleartext_rejected"):
            policy.validate_url("http://example.test/mcp")

    def test_redirect_credentials_never_forward_cross_origin_when_allowed(self):
        policy = NetworkSecurityPolicy(allow_cross_origin_redirects=True)
        headers = policy.redirect_headers(
            "https://api.example.test/a",
            "https://other.example.test/b",
            {
                "Authorization": "Bearer synthetic-secret",
                "Host": "api.example.test",
                "X-Trace": "ok",
                "Cookie": "secret",
            },
        )
        self.assertEqual({"X-Trace": "ok"}, headers)

    def test_cross_origin_redirect_rejected_by_default(self):
        policy = NetworkSecurityPolicy()
        with self.assertRaisesRegex(
            SecurityBoundaryError, "network_cross_origin_redirect_rejected"
        ):
            policy.validate_redirect(
                "https://api.example.test/a", "https://other.example.test/b"
            )

    def test_proxy_spoofing_ignored_from_untrusted_peer(self):
        policy = NetworkSecurityPolicy(trusted_proxy_networks=("10.0.0.0/8",))
        self.assertEqual(
            "203.0.113.8",
            policy.resolve_client_ip(
                "203.0.113.8", {"X-Forwarded-For": "10.10.10.10"}
            ),
        )

    def test_trusted_proxy_can_supply_valid_forwarded_address(self):
        policy = NetworkSecurityPolicy(trusted_proxy_networks=("10.0.0.0/8",))
        self.assertEqual(
            "198.51.100.4",
            policy.resolve_client_ip(
                "10.1.2.3", {"X-Forwarded-For": "198.51.100.4, 10.1.2.3"}
            ),
        )

    def test_trusted_proxy_chain_uses_first_untrusted_hop_from_right(self):
        policy = NetworkSecurityPolicy(trusted_proxy_networks=("10.0.0.0/8",))
        self.assertEqual(
            "203.0.113.7",
            policy.resolve_client_ip(
                "10.1.2.3",
                {"X-Forwarded-For": "198.51.100.99, 203.0.113.7, 10.2.3.4"},
            ),
        )

    def test_malicious_exception_message_is_not_exposed(self):
        error = safe_error(
            RuntimeError("Authorization: Bearer synthetic-secret"),
            code="provider_failed",
        )
        self.assertNotIn("synthetic-secret", str(error))
        self.assertEqual("RuntimeError", error.error_type)

    def test_text_redaction_removes_headers_and_bearer_tokens(self):
        text = redact_sensitive_text(
            "Authorization: Bearer synthetic-secret\n"
            "X-Api-Key: abcdefghijklmnop\n"
            "elsewhere Bearer abc.def.ghi"
        )
        self.assertNotIn("synthetic-secret", text)
        self.assertNotIn("abcdefghijklmnop", text)
        self.assertNotIn("abc.def.ghi", text)


if __name__ == "__main__":
    unittest.main()
