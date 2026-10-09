from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Sequence

from ..security import (
    HS256JWTValidator,
    JWTValidationConfig,
    MountedFileSecretProvider,
    SecurityBoundaryError,
)


@dataclass(frozen=True, slots=True)
class ServiceAuthConfig:
    """Application-owned API caller authentication and route authorization policy.

    Authentication proves an API caller identity at the network boundary. JWT
    mode separately requires an application-owned capability before the caller
    may use the run endpoint. Neither step grants Manager tool authorization or
    human approval.
    """

    mode: str
    secret_name: str
    issuer: str | None = None
    audience: str | None = None
    principal_type: str = "api_client"
    required_capability: str = "manager.run"
    clock_skew_seconds: int = 60
    require_jti: bool = True

    def __post_init__(self) -> None:
        if self.mode not in {"none", "bearer", "jwt_hs256"}:
            raise ValueError("service authentication mode is unsupported")
        if not isinstance(self.secret_name, str) or not self.secret_name:
            raise ValueError("service authentication secret name must be non-empty text")
        if not isinstance(self.principal_type, str) or not self.principal_type:
            raise ValueError("service principal type must be non-empty text")
        if not isinstance(self.required_capability, str) or not self.required_capability:
            raise ValueError("service required capability must be non-empty text")
        if (
            not isinstance(self.clock_skew_seconds, int)
            or isinstance(self.clock_skew_seconds, bool)
            or self.clock_skew_seconds < 0
            or self.clock_skew_seconds > 600
        ):
            raise ValueError("service authentication clock skew must be between 0 and 600 seconds")
        if not isinstance(self.require_jti, bool):
            raise TypeError("service authentication require_jti must be boolean")
        if self.mode == "jwt_hs256":
            if not isinstance(self.issuer, str) or not self.issuer:
                raise ValueError("JWT service authentication requires a non-empty issuer")
            if not isinstance(self.audience, str) or not self.audience:
                raise ValueError("JWT service authentication requires a non-empty audience")


class ServiceAuthenticator:
    """Authenticate and route-authorize one API request without widening Manager authority."""

    def __init__(self, secrets_dir: str | None, config: ServiceAuthConfig) -> None:
        self.config = config
        self.provider = None
        self.validator = None
        if config.mode != "none":
            if not secrets_dir:
                raise ValueError("authenticated service modes require a secrets directory")
            self.provider = MountedFileSecretProvider(secrets_dir)
        if config.mode == "jwt_hs256":
            assert self.provider is not None
            assert config.issuer is not None
            assert config.audience is not None
            self.validator = HS256JWTValidator(
                JWTValidationConfig(
                    issuers=frozenset({config.issuer}),
                    audiences=frozenset({config.audience}),
                    allowed_algorithms=frozenset({"HS256"}),
                    clock_skew_seconds=config.clock_skew_seconds,
                    require_nbf=True,
                    require_jti=config.require_jti,
                ),
                self.provider,
                secret_name=config.secret_name,
            )

    def ready(self) -> bool | tuple[bool, str]:
        if self.config.mode == "none":
            return True
        try:
            assert self.provider is not None
            value = self.provider.acquire(self.config.secret_name).reveal()
            if self.config.mode == "jwt_hs256" and len(value) < 32:
                return False, "service JWT verification key is too weak"
        except Exception:
            return False, "service credential unavailable"
        return True

    def require_available(self) -> None:
        result = self.ready()
        ok = result if isinstance(result, bool) else result[0]
        if not ok:
            raise ValueError("service authentication credential is unavailable")

    @staticmethod
    def _token(headers: Sequence[str]) -> str | None:
        # Multiple Authorization headers are ambiguous across HTTP stacks and
        # proxies. Reject rather than relying on first/last-header behavior.
        if len(headers) != 1:
            return None
        header = headers[0]
        if not isinstance(header, str) or len(header) > 16384:
            return None
        if not header.startswith("Bearer "):
            return None
        token = header[7:]
        if not token or token.strip() != token:
            return None
        return token

    def authenticate(self, headers: Sequence[str]) -> dict[str, object] | None:
        if self.config.mode == "none":
            # Development/test mode intentionally has no authenticated identity.
            # Authorization-sensitive runtime paths must not interpret this as a
            # grant of scope or approval.
            return {
                "authenticated": False,
                "authorized": False,
                "principal_type": "anonymous",
                "authentication_method": "none",
            }

        token = self._token(headers)
        if token is None:
            return None

        if self.config.mode == "bearer":
            # Static bearer mode is a compatibility API-key boundary: possession
            # of the application-owned secret authenticates and authorizes this
            # one service route, but never becomes Manager tool authority.
            try:
                assert self.provider is not None
                expected = self.provider.acquire(self.config.secret_name).reveal()
                supplied = token.encode("utf-8", errors="strict")
            except (SecurityBoundaryError, OSError, UnicodeError, ValueError):
                return None
            if not hmac.compare_digest(supplied, expected):
                return None
            return {
                "authenticated": True,
                "authorized": True,
                "principal_type": self.config.principal_type,
                "authentication_method": "static_bearer",
            }

        assert self.validator is not None
        try:
            identity = self.validator.validate(
                token,
                principal_type=self.config.principal_type,
            )
        except (SecurityBoundaryError, TypeError, ValueError):
            return None

        capabilities = identity.get("capabilities")
        authorized = (
            isinstance(capabilities, list)
            and self.config.required_capability in capabilities
        )
        return {
            "authenticated": True,
            "authorized": authorized,
            "authentication_method": "jwt_hs256",
            **identity,
        }
