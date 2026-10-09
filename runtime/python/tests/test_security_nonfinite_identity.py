from __future__ import annotations

import base64
import hashlib
import hmac
import json
import unittest

from manager_runtime.security import (
    HS256JWTValidator,
    JWTValidationConfig,
    SecretLease,
    SecurityBoundaryError,
    evaluate_tool_authorization,
)

NOW = 2_000_000_000.0
KEY = b"synthetic-security-signing-key-32-bytes!!"


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


class Provider:
    def acquire(self, name: str) -> SecretLease:
        if name != "jwt-primary":
            raise SecurityBoundaryError("credential_unavailable")
        return SecretLease(KEY)


def raw_token(claims: bytes) -> str:
    header = b64(b'{"alg":"HS256","typ":"JWT","kid":"primary"}')
    body = b64(claims)
    signature = hmac.new(KEY, f"{header}.{body}".encode("ascii"), hashlib.sha256).digest()
    return f"{header}.{body}.{b64(signature)}"


def claims(**overrides) -> dict:
    value = {
        "iss": "https://issuer.example",
        "aud": "manager-api",
        "sub": "worker-17",
        "exp": NOW + 300,
        "nbf": NOW - 5,
        "iat": NOW - 30,
        "jti": "token-1",
        "scope": "tools.destroy",
    }
    value.update(overrides)
    return value


def validator(*, require_nbf: bool = True, clock=lambda: NOW) -> HS256JWTValidator:
    return HS256JWTValidator(
        JWTValidationConfig(
            issuers=frozenset({"https://issuer.example"}),
            audiences=frozenset({"manager-api"}),
            clock_skew_seconds=0,
            require_nbf=require_nbf,
            require_jti=True,
        ),
        Provider(),
        secret_name="jwt-primary",
        clock=clock,
    )


class SecurityNonFiniteIdentityTests(unittest.TestCase):
    def test_signed_nan_expiry_is_rejected_as_malformed_json(self) -> None:
        malformed = raw_token(
            b'{"iss":"https://issuer.example","aud":"manager-api",'
            b'"sub":"worker-17","exp":NaN,"nbf":1999999995,'
            b'"iat":1999999970,"jti":"token-1","scope":"tools.destroy"}'
        )
        with self.assertRaisesRegex(SecurityBoundaryError, "identity_claims_malformed"):
            validator().validate(malformed)

    def test_signed_infinite_expiry_is_rejected_as_malformed_json(self) -> None:
        malformed = raw_token(
            b'{"iss":"https://issuer.example","aud":"manager-api",'
            b'"sub":"worker-17","exp":Infinity,"nbf":1999999995,'
            b'"iat":1999999970,"jti":"token-1","scope":"tools.destroy"}'
        )
        with self.assertRaisesRegex(SecurityBoundaryError, "identity_claims_malformed"):
            validator().validate(malformed)

    def test_optional_nbf_still_rejects_malformed_claim_when_present(self) -> None:
        jwt = raw_token(json.dumps(claims(nbf="not-a-time"), separators=(",", ":")).encode())
        with self.assertRaisesRegex(SecurityBoundaryError, "identity_nbf_invalid"):
            validator(require_nbf=False).validate(jwt)

    def test_nonfinite_embedding_identity_expiry_fails_closed(self) -> None:
        identity = {
            "subject": "worker-17",
            "issuer": "https://issuer.example",
            "audiences": ["manager-api"],
            "principal_type": "worker",
            "capabilities": ["tools.destroy"],
            "expires_at": float("nan"),
            "not_before": 0.0,
            "issued_at": NOW - 30,
            "token_id": "token-1",
            "algorithm": "HS256",
        }
        authorization = {
            "security_context": {"principal": identity, "environment": "prod"},
            "security_policy": {
                "revision": "r1",
                "rules": [
                    {
                        "capability": "tools.destroy",
                        "actions": ["tool:destroy"],
                        "resources": ["project:*"],
                        "environments": ["prod"],
                        "side_effect_classes": ["sensitive_destructive"],
                    }
                ],
            },
        }
        with self.assertRaisesRegex(SecurityBoundaryError, "security_identity_expiry_invalid"):
            evaluate_tool_authorization(
                authorization,
                {"tool_name": "destroy", "target": "project:7"},
                {"side_effect_class": "sensitive_destructive"},
                now=NOW,
            )

    def test_nonfinite_security_clock_fails_closed(self) -> None:
        valid_identity = {
            "subject": "worker-17",
            "issuer": "https://issuer.example",
            "audiences": ["manager-api"],
            "principal_type": "worker",
            "capabilities": ["tools.destroy"],
            "expires_at": NOW + 300,
            "not_before": NOW - 5,
            "issued_at": NOW - 30,
            "token_id": "token-1",
            "algorithm": "HS256",
        }
        authorization = {
            "security_context": {"principal": valid_identity, "environment": "prod"},
            "security_policy": {"revision": "r1", "rules": []},
        }
        with self.assertRaisesRegex(SecurityBoundaryError, "security_clock_invalid"):
            evaluate_tool_authorization(
                authorization,
                {"tool_name": "destroy", "target": "project:7"},
                {"side_effect_class": "sensitive_destructive"},
                now=float("nan"),
            )

    def test_nonfinite_secret_lease_expiry_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "finite"):
            SecretLease(b"synthetic-secret", expires_at=float("nan"))


if __name__ == "__main__":
    unittest.main()
