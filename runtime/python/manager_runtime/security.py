from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import math
import os
import re
import ssl
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence, runtime_checkable
from urllib.parse import urlsplit


class SecurityBoundaryError(RuntimeError):
    """Public-safe security failure with no secret-bearing source message."""

    def __init__(self, code: str, *, error_type: str | None = None) -> None:
        if not isinstance(code, str) or not code:
            raise ValueError("security error code must be non-empty text")
        self.code = code
        self.error_type = error_type
        suffix = f" ({error_type})" if error_type else ""
        super().__init__(f"{code}{suffix}")


@dataclass(frozen=True)
class SecretLease:
    """Ephemeral secret material that refuses accidental representation/persistence."""

    _value: bytes
    expires_at: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self._value, bytes) or not self._value:
            raise ValueError("secret lease value must be non-empty bytes")
        if self.expires_at is not None:
            if not isinstance(self.expires_at, (int, float)) or isinstance(self.expires_at, bool):
                raise TypeError("secret lease expiry must be numeric when provided")
            if not math.isfinite(float(self.expires_at)):
                raise ValueError("secret lease expiry must be finite when provided")

    def reveal(self, *, now: float | None = None) -> bytes:
        current = time.time() if now is None else float(now)
        if not math.isfinite(current):
            raise SecurityBoundaryError("credential_clock_invalid")
        if self.expires_at is not None and current >= float(self.expires_at):
            raise SecurityBoundaryError("credential_expired")
        return self._value

    def __repr__(self) -> str:
        return "SecretLease(<redacted>)"

    def __str__(self) -> str:
        return "<redacted>"

    def __getstate__(self) -> object:
        raise TypeError("secret leases must not be serialized")


@runtime_checkable
class SecretProvider(Protocol):
    def acquire(self, name: str) -> SecretLease:
        """Return a fresh credential lease for the named secret."""


class EnvironmentSecretProvider:
    """Read externally injected secrets from environment variables on demand."""

    def __init__(self, *, prefix: str = "") -> None:
        if not isinstance(prefix, str):
            raise TypeError("secret environment prefix must be text")
        self.prefix = prefix

    def acquire(self, name: str) -> SecretLease:
        _validate_secret_name(name)
        key = f"{self.prefix}{name}"
        value = os.environ.get(key)
        if not value:
            raise SecurityBoundaryError("credential_unavailable")
        return SecretLease(value.encode("utf-8"))


class MountedFileSecretProvider:
    """Read a secret from a mounted directory without accepting arbitrary paths."""

    def __init__(self, directory: str | os.PathLike[str]) -> None:
        root = Path(directory).expanduser().resolve()
        self.directory = root

    def acquire(self, name: str) -> SecretLease:
        _validate_secret_name(name)
        path = (self.directory / name).resolve()
        try:
            path.relative_to(self.directory)
        except ValueError as exc:
            raise SecurityBoundaryError("credential_path_rejected") from exc
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise SecurityBoundaryError(
                "credential_unavailable", error_type=type(exc).__name__
            ) from exc
        data = data.rstrip(b"\r\n")
        if not data:
            raise SecurityBoundaryError("credential_unavailable")
        return SecretLease(data)


class ChainedSecretProvider:
    """Try providers in explicit application-owned order without exposing values."""

    def __init__(self, providers: Sequence[SecretProvider]) -> None:
        if not providers:
            raise ValueError("at least one secret provider is required")
        if not all(isinstance(item, SecretProvider) for item in providers):
            raise TypeError("all secret providers must implement acquire()")
        self.providers = tuple(providers)

    def acquire(self, name: str) -> SecretLease:
        last: BaseException | None = None
        for provider in self.providers:
            try:
                return provider.acquire(name)
            except SecurityBoundaryError as exc:
                if exc.code != "credential_unavailable":
                    raise
                last = exc
            except Exception as exc:
                raise SecurityBoundaryError(
                    "credential_provider_failed", error_type=type(exc).__name__
                ) from exc
        raise SecurityBoundaryError("credential_unavailable") from last


def _validate_secret_name(name: str) -> None:
    if not isinstance(name, str) or not name:
        raise ValueError("secret name must be non-empty text")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise ValueError("secret name contains unsupported characters")


def _b64url_decode(value: str) -> bytes:
    if not isinstance(value, str) or not value:
        raise SecurityBoundaryError("identity_token_malformed")
    if re.fullmatch(r"[A-Za-z0-9_-]+", value) is None:
        raise SecurityBoundaryError("identity_token_malformed")
    padding = "=" * ((4 - len(value) % 4) % 4)
    try:
        return base64.b64decode(
            (value + padding).encode("ascii"), altchars=b"-_", validate=True
        )
    except (ValueError, UnicodeError) as exc:
        raise SecurityBoundaryError("identity_token_malformed") from exc


def _strict_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant {value!r}")


def _json_segment(value: str, *, kind: str) -> dict[str, Any]:
    try:
        decoded = json.loads(
            _b64url_decode(value).decode("utf-8"),
            object_pairs_hook=_strict_object_pairs,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise SecurityBoundaryError(f"identity_{kind}_malformed") from exc
    if not isinstance(decoded, dict):
        raise SecurityBoundaryError(f"identity_{kind}_malformed")
    return decoded


def _finite_numeric(value: Any, *, code: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise SecurityBoundaryError(code)
    result = float(value)
    if not math.isfinite(result):
        raise SecurityBoundaryError(code)
    return result


def _numeric_claim(claims: Mapping[str, Any], name: str) -> float:
    return _finite_numeric(claims.get(name), code=f"identity_{name}_invalid")


def _audiences(claims: Mapping[str, Any]) -> tuple[str, ...]:
    value = claims.get("aud")
    if isinstance(value, str) and value:
        return (value,)
    if isinstance(value, list) and value and all(isinstance(item, str) and item for item in value):
        if len(set(value)) != len(value):
            raise SecurityBoundaryError("identity_audience_invalid")
        return tuple(value)
    raise SecurityBoundaryError("identity_audience_invalid")


def _capabilities(claims: Mapping[str, Any]) -> tuple[str, ...]:
    found: list[str] = []
    raw_caps = claims.get("capabilities")
    if raw_caps is not None:
        if not isinstance(raw_caps, list) or not all(
            isinstance(item, str) and item for item in raw_caps
        ):
            raise SecurityBoundaryError("identity_capabilities_invalid")
        found.extend(raw_caps)
    raw_scope = claims.get("scope")
    if raw_scope is not None:
        if not isinstance(raw_scope, str):
            raise SecurityBoundaryError("identity_scope_invalid")
        found.extend(item for item in raw_scope.split() if item)
    return tuple(sorted(set(found)))


@dataclass(frozen=True)
class JWTValidationConfig:
    issuers: frozenset[str]
    audiences: frozenset[str]
    allowed_algorithms: frozenset[str] = frozenset({"HS256"})
    clock_skew_seconds: int = 60
    require_nbf: bool = True
    require_jti: bool = False
    max_token_chars: int = 16384
    min_hmac_key_bytes: int = 32

    def __post_init__(self) -> None:
        if not self.issuers or not all(isinstance(item, str) and item for item in self.issuers):
            raise ValueError("at least one expected issuer is required")
        if not self.audiences or not all(
            isinstance(item, str) and item for item in self.audiences
        ):
            raise ValueError("at least one expected audience is required")
        if not self.allowed_algorithms:
            raise ValueError("at least one allowed token algorithm is required")
        if self.allowed_algorithms - {"HS256"}:
            raise ValueError("the zero-dependency reference validator currently supports HS256 only")
        if not isinstance(self.clock_skew_seconds, int) or self.clock_skew_seconds < 0:
            raise ValueError("clock skew must be a non-negative integer")
        if not isinstance(self.max_token_chars, int) or self.max_token_chars < 256:
            raise ValueError("maximum token size must be at least 256 characters")
        if not isinstance(self.min_hmac_key_bytes, int) or self.min_hmac_key_bytes < 32:
            raise ValueError("HS256 keys must require at least 32 bytes")


@runtime_checkable
class RevocationChecker(Protocol):
    def is_revoked(self, identity: Mapping[str, Any]) -> bool:
        """Return True when the authenticated identity has been revoked."""


class HS256JWTValidator:
    """Small fail-closed JWT validator for reference/runtime boundary tests.

    Production applications may provide a different verifier for asymmetric or
    workload-identity tokens, but must preserve equivalent issuer/audience/time/
    algorithm/capability and revocation checks.
    """

    def __init__(
        self,
        config: JWTValidationConfig,
        key_provider: SecretProvider,
        *,
        secret_name: str | Callable[[str | None], str],
        revocation_checker: RevocationChecker | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.config = config
        self.key_provider = key_provider
        self.secret_name = secret_name
        self.revocation_checker = revocation_checker
        self.clock = clock

    def validate(self, token: str, *, principal_type: str = "human") -> dict[str, Any]:
        if (
            not isinstance(token, str)
            or token.count(".") != 2
            or len(token) > self.config.max_token_chars
        ):
            raise SecurityBoundaryError("identity_token_malformed")
        encoded_header, encoded_claims, encoded_signature = token.split(".")
        header = _json_segment(encoded_header, kind="header")
        claims = _json_segment(encoded_claims, kind="claims")

        alg = header.get("alg")
        if not isinstance(alg, str) or alg not in self.config.allowed_algorithms:
            raise SecurityBoundaryError("identity_algorithm_rejected")
        if alg == "none":
            raise SecurityBoundaryError("identity_algorithm_rejected")
        if "crit" in header or "zip" in header or header.get("b64") is False:
            raise SecurityBoundaryError("identity_header_extension_rejected")

        kid = header.get("kid")
        if kid is not None and (not isinstance(kid, str) or not kid):
            raise SecurityBoundaryError("identity_key_id_invalid")
        try:
            secret_name = self.secret_name(kid) if callable(self.secret_name) else self.secret_name
        except Exception as exc:
            raise SecurityBoundaryError(
                "credential_selector_failed", error_type=type(exc).__name__
            ) from exc
        _validate_secret_name(secret_name)
        try:
            lease = self.key_provider.acquire(secret_name)
        except SecurityBoundaryError:
            raise
        except Exception as exc:
            raise SecurityBoundaryError(
                "credential_lookup_failed", error_type=type(exc).__name__
            ) from exc
        if not isinstance(lease, SecretLease):
            raise SecurityBoundaryError("credential_provider_contract_invalid")
        key = lease.reveal(now=self.clock())
        if len(key) < self.config.min_hmac_key_bytes:
            raise SecurityBoundaryError("identity_signing_key_too_weak")

        signing_input = f"{encoded_header}.{encoded_claims}".encode("ascii")
        expected = hmac.new(key, signing_input, hashlib.sha256).digest()
        signature = _b64url_decode(encoded_signature)
        if not hmac.compare_digest(signature, expected):
            raise SecurityBoundaryError("identity_signature_invalid")

        issuer = claims.get("iss")
        if not isinstance(issuer, str) or issuer not in self.config.issuers:
            raise SecurityBoundaryError("identity_issuer_rejected")
        audiences = _audiences(claims)
        if not self.config.audiences.intersection(audiences):
            raise SecurityBoundaryError("identity_audience_rejected")

        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise SecurityBoundaryError("identity_subject_invalid")
        if not isinstance(principal_type, str) or not principal_type:
            raise SecurityBoundaryError("identity_principal_type_invalid")

        now = _finite_numeric(self.clock(), code="identity_clock_invalid")
        skew = float(self.config.clock_skew_seconds)
        expires_at = _numeric_claim(claims, "exp")
        if now - skew >= expires_at:
            raise SecurityBoundaryError("identity_expired")
        if self.config.require_nbf:
            not_before = _numeric_claim(claims, "nbf")
        else:
            not_before = 0.0 if claims.get("nbf") is None else _numeric_claim(claims, "nbf")
        if now + skew < not_before:
            raise SecurityBoundaryError("identity_not_yet_valid")
        issued_at = claims.get("iat")
        if issued_at is not None:
            issued_at = _numeric_claim(claims, "iat")
            if issued_at > now + skew:
                raise SecurityBoundaryError("identity_iat_invalid")

        token_id = claims.get("jti")
        if self.config.require_jti and (not isinstance(token_id, str) or not token_id):
            raise SecurityBoundaryError("identity_token_id_invalid")
        if token_id is not None and (not isinstance(token_id, str) or not token_id):
            raise SecurityBoundaryError("identity_token_id_invalid")

        identity = {
            "subject": subject,
            "issuer": issuer,
            "audiences": list(audiences),
            "principal_type": principal_type,
            "capabilities": list(_capabilities(claims)),
            "expires_at": expires_at,
            "not_before": not_before,
            "issued_at": issued_at,
            "token_id": token_id,
            "algorithm": alg,
        }
        if self.revocation_checker is not None:
            try:
                revoked = self.revocation_checker.is_revoked(identity)
            except SecurityBoundaryError:
                raise
            except Exception as exc:
                raise SecurityBoundaryError(
                    "identity_revocation_check_failed", error_type=type(exc).__name__
                ) from exc
            if not isinstance(revoked, bool):
                raise SecurityBoundaryError("identity_revocation_result_invalid")
            if revoked:
                raise SecurityBoundaryError("identity_revoked")
        return identity


def _canonical_digest(value: Any) -> str:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SecurityBoundaryError("security_context_not_canonical") from exc
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _match_policy_pattern(value: str, pattern: str) -> bool:
    if pattern == "*":
        return True
    if pattern.endswith("*") and pattern.count("*") == 1:
        return value.startswith(pattern[:-1])
    return value == pattern


def _normalized_string_list(value: Any, *, field: str, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise SecurityBoundaryError(f"security_{field}_invalid")
    if not allow_empty and not value:
        raise SecurityBoundaryError(f"security_{field}_invalid")
    if len(set(value)) != len(value):
        raise SecurityBoundaryError(f"security_{field}_invalid")
    return sorted(value)


def _validated_identity(identity: Any, *, now: float, skew: float) -> dict[str, Any]:
    if not isinstance(identity, dict):
        raise SecurityBoundaryError("security_identity_missing")
    required_text = ("subject", "issuer", "principal_type", "algorithm")
    for field in required_text:
        if not isinstance(identity.get(field), str) or not identity[field]:
            raise SecurityBoundaryError(f"security_identity_{field}_invalid")
    audiences = _normalized_string_list(identity.get("audiences"), field="identity_audiences")
    capabilities = _normalized_string_list(
        identity.get("capabilities", []), field="identity_capabilities", allow_empty=True
    )
    expires_at = _finite_numeric(
        identity.get("expires_at"), code="security_identity_expiry_invalid"
    )
    not_before = _finite_numeric(
        identity.get("not_before"), code="security_identity_not_before_invalid"
    )
    if now - skew >= expires_at:
        raise SecurityBoundaryError("security_identity_expired")
    if now + skew < not_before:
        raise SecurityBoundaryError("security_identity_not_yet_valid")
    token_id = identity.get("token_id")
    if token_id is not None and (not isinstance(token_id, str) or not token_id):
        raise SecurityBoundaryError("security_identity_token_id_invalid")
    issued_at = identity.get("issued_at")
    if issued_at is not None:
        issued_at = _finite_numeric(
            issued_at, code="security_identity_issued_at_invalid"
        )
        if issued_at > now + skew:
            raise SecurityBoundaryError("security_identity_issued_at_invalid")
    return {
        "subject": identity["subject"],
        "issuer": identity["issuer"],
        "audiences": audiences,
        "principal_type": identity["principal_type"],
        "capabilities": capabilities,
        "expires_at": expires_at,
        "not_before": not_before,
        "issued_at": issued_at,
        "token_id": token_id,
        "algorithm": identity["algorithm"],
    }


def evaluate_tool_authorization(
    authorization: Mapping[str, Any],
    request: Mapping[str, Any],
    definition: Mapping[str, Any],
    *,
    now: float | None = None,
) -> dict[str, Any] | None:
    """Deny-by-default principal/capability authorization for one tool action.

    Returns None when the caller intentionally uses the legacy coarse-grained
    authorization context. When either `security_context` or `security_policy`
    appears, both are required and this stricter boundary becomes authoritative.
    """
    has_context = "security_context" in authorization
    has_policy = "security_policy" in authorization
    require_security = authorization.get("require_security_context", False)
    if not isinstance(require_security, bool):
        raise SecurityBoundaryError("security_requirement_invalid")
    if not has_context and not has_policy:
        if require_security:
            raise SecurityBoundaryError("security_context_required")
        return None
    if not has_context or not has_policy:
        raise SecurityBoundaryError("security_context_incomplete")

    context = authorization.get("security_context")
    policy = authorization.get("security_policy")
    if not isinstance(context, dict) or not isinstance(policy, dict):
        raise SecurityBoundaryError("security_context_invalid")
    environment = context.get("environment")
    if not isinstance(environment, str) or not environment:
        raise SecurityBoundaryError("security_environment_invalid")

    revision = policy.get("revision")
    if not isinstance(revision, str) or not revision:
        raise SecurityBoundaryError("security_policy_revision_invalid")
    skew = policy.get("clock_skew_seconds", 60)
    if not isinstance(skew, int) or isinstance(skew, bool) or skew < 0:
        raise SecurityBoundaryError("security_policy_clock_skew_invalid")
    current = _finite_numeric(
        time.time() if now is None else now,
        code="security_clock_invalid",
    )
    identity = _validated_identity(context.get("principal"), now=current, skew=float(skew))

    principal_revalidator = context.get("principal_revalidator")
    if principal_revalidator is not None:
        if not callable(principal_revalidator):
            raise SecurityBoundaryError("security_principal_revalidator_invalid")
        try:
            principal_current = principal_revalidator(dict(identity))
        except SecurityBoundaryError:
            raise
        except Exception as exc:
            raise SecurityBoundaryError(
                "security_principal_revalidation_failed",
                error_type=type(exc).__name__,
            ) from exc
        if not isinstance(principal_current, bool):
            raise SecurityBoundaryError("security_principal_revalidation_result_invalid")
        if not principal_current:
            return {
                "allowed": False,
                "reason": "principal_not_current",
                "policy_revision": revision,
            }

    allowed_principal_types = policy.get("allowed_principal_types")
    if allowed_principal_types is not None:
        types = _normalized_string_list(
            allowed_principal_types, field="policy_principal_types"
        )
        if identity["principal_type"] not in types:
            return {
                "allowed": False,
                "reason": "principal_type_not_allowed",
                "policy_revision": revision,
            }

    max_age = policy.get("max_authorization_age_seconds")
    if max_age is not None:
        if not isinstance(max_age, int) or isinstance(max_age, bool) or max_age < 0:
            raise SecurityBoundaryError("security_policy_max_age_invalid")
        issued_at = identity.get("issued_at")
        if issued_at is None or current - float(issued_at) > max_age + skew:
            return {
                "allowed": False,
                "reason": "authentication_too_old",
                "policy_revision": revision,
            }

    tool_name = request.get("tool_name")
    if not isinstance(tool_name, str) or not tool_name:
        raise SecurityBoundaryError("security_action_invalid")
    action = f"tool:{tool_name}"
    target = request.get("target")
    resource = target if isinstance(target, str) and target else f"tool:{tool_name}"
    side_effect_class = definition.get("side_effect_class")
    if not isinstance(side_effect_class, str) or not side_effect_class:
        raise SecurityBoundaryError("security_side_effect_class_invalid")

    rules = policy.get("rules")
    if not isinstance(rules, list):
        raise SecurityBoundaryError("security_policy_rules_invalid")

    matched_capability: str | None = None
    for rule in rules:
        if not isinstance(rule, dict):
            raise SecurityBoundaryError("security_policy_rule_invalid")
        capability = rule.get("capability")
        if not isinstance(capability, str) or not capability:
            raise SecurityBoundaryError("security_policy_capability_invalid")
        actions = _normalized_string_list(rule.get("actions"), field="policy_actions")
        resources = _normalized_string_list(rule.get("resources"), field="policy_resources")
        environments = _normalized_string_list(
            rule.get("environments"), field="policy_environments"
        )
        side_effects = _normalized_string_list(
            rule.get("side_effect_classes"), field="policy_side_effect_classes"
        )
        if capability not in identity["capabilities"]:
            continue
        if not any(_match_policy_pattern(action, pattern) for pattern in actions):
            continue
        if not any(_match_policy_pattern(resource, pattern) for pattern in resources):
            continue
        if environment not in environments and "*" not in environments:
            continue
        if side_effect_class not in side_effects and "*" not in side_effects:
            continue
        matched_capability = capability
        break

    if matched_capability is None:
        return {
            "allowed": False,
            "reason": "capability_policy_denied",
            "policy_revision": revision,
        }

    binding_payload = {
        "principal": identity,
        "environment": environment,
        "policy_revision": revision,
        "capability": matched_capability,
        "action": action,
        "resource": resource,
        "side_effect_class": side_effect_class,
    }
    return {
        "allowed": True,
        "reason": "capability_policy_allowed",
        "policy_revision": revision,
        "capability": matched_capability,
        "principal_subject": identity["subject"],
        "binding": _canonical_digest(binding_payload),
    }


SENSITIVE_HEADER_NAMES = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        "api-key",
        "host",
    }
)


@dataclass(frozen=True)
class NetworkSecurityPolicy:
    """Provider-neutral network trust policy for production transport adapters."""

    production: bool = False
    verify_tls: bool = True
    ca_file: str | None = None
    client_cert_file: str | None = None
    client_key_file: str | None = None
    allow_loopback_http: bool = False
    allow_cross_origin_redirects: bool = False
    trusted_proxy_networks: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.production and not self.verify_tls:
            raise ValueError("production network policy cannot disable TLS verification")
        if bool(self.client_cert_file) != bool(self.client_key_file):
            raise ValueError("mTLS client certificate and key must be configured together")
        for network in self.trusted_proxy_networks:
            try:
                ipaddress.ip_network(network, strict=False)
            except ValueError as exc:
                raise ValueError(f"invalid trusted proxy network: {network}") from exc

    def validate_url(self, url: str) -> None:
        if not isinstance(url, str) or not url:
            raise SecurityBoundaryError("network_url_invalid")
        parsed = urlsplit(url)
        if parsed.username is not None or parsed.password is not None:
            raise SecurityBoundaryError("network_url_credentials_rejected")
        if not parsed.hostname:
            raise SecurityBoundaryError("network_url_host_missing")
        scheme = parsed.scheme.lower()
        if scheme == "https":
            return
        if scheme != "http":
            raise SecurityBoundaryError("network_scheme_rejected")
        try:
            host = ipaddress.ip_address(parsed.hostname)
            loopback = host.is_loopback
        except ValueError:
            loopback = parsed.hostname.lower() == "localhost"
        if not (self.allow_loopback_http and loopback and not self.production):
            raise SecurityBoundaryError("network_cleartext_rejected")

    def validate_redirect(self, source_url: str, target_url: str) -> None:
        self.validate_url(source_url)
        self.validate_url(target_url)
        source = urlsplit(source_url)
        target = urlsplit(target_url)
        source_origin = (source.scheme.lower(), source.hostname, source.port or _default_port(source.scheme))
        target_origin = (target.scheme.lower(), target.hostname, target.port or _default_port(target.scheme))
        if source.scheme.lower() == "https" and target.scheme.lower() != "https":
            raise SecurityBoundaryError("network_redirect_downgrade_rejected")
        if source_origin != target_origin and not self.allow_cross_origin_redirects:
            raise SecurityBoundaryError("network_cross_origin_redirect_rejected")

    def redirect_headers(
        self, source_url: str, target_url: str, headers: Mapping[str, str]
    ) -> dict[str, str]:
        self.validate_redirect(source_url, target_url)
        source = urlsplit(source_url)
        target = urlsplit(target_url)
        source_origin = (source.scheme.lower(), source.hostname, source.port or _default_port(source.scheme))
        target_origin = (target.scheme.lower(), target.hostname, target.port or _default_port(target.scheme))
        copied = dict(headers)
        if source_origin != target_origin:
            copied = {
                key: value
                for key, value in copied.items()
                if key.lower() not in SENSITIVE_HEADER_NAMES
            }
        return copied

    def ssl_context(self) -> ssl.SSLContext:
        try:
            if not self.verify_tls:
                if self.production:
                    raise SecurityBoundaryError("network_tls_verification_required")
                context = ssl.create_default_context()
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
                return context
            context = ssl.create_default_context(cafile=self.ca_file)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.check_hostname = True
            context.verify_mode = ssl.CERT_REQUIRED
            if self.client_cert_file and self.client_key_file:
                context.load_cert_chain(self.client_cert_file, self.client_key_file)
            return context
        except SecurityBoundaryError:
            raise
        except (OSError, ssl.SSLError) as exc:
            raise SecurityBoundaryError(
                "network_tls_configuration_failed", error_type=type(exc).__name__
            ) from exc

    def resolve_client_ip(self, peer_ip: str, headers: Mapping[str, str]) -> str:
        try:
            peer = ipaddress.ip_address(peer_ip)
        except ValueError as exc:
            raise SecurityBoundaryError("network_peer_ip_invalid") from exc
        networks = tuple(
            ipaddress.ip_network(network, strict=False)
            for network in self.trusted_proxy_networks
        )
        trusted_peer = any(peer in network for network in networks)
        forwarded = _header(headers, "x-forwarded-for")
        if not trusted_peer or not forwarded:
            return str(peer)

        chain = []
        for raw in forwarded.split(","):
            value = raw.strip()
            if not value:
                raise SecurityBoundaryError("network_forwarded_for_invalid")
            try:
                chain.append(ipaddress.ip_address(value))
            except ValueError as exc:
                raise SecurityBoundaryError("network_forwarded_for_invalid") from exc
        chain.append(peer)

        # Walk from the immediate peer toward the client. Trusted proxies are
        # discarded from the right; the first untrusted hop is the attributable
        # client. This prevents a caller-controlled leftmost XFF value from
        # winning merely because the direct peer is trusted.
        for candidate in reversed(chain):
            if any(candidate in network for network in networks):
                continue
            return str(candidate)
        return str(chain[0])


def _default_port(scheme: str) -> int | None:
    return 443 if scheme.lower() == "https" else 80 if scheme.lower() == "http" else None


def _header(headers: Mapping[str, str], name: str) -> str | None:
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return value
    return None


_BEARER_RE = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/-]+=*")
_HEADER_RE = re.compile(
    r"(?im)^(authorization|proxy-authorization|cookie|set-cookie|x-api-key|api-key)\s*:\s*.*$"
)
_TOKENISH_RE = re.compile(r"(?i)\b(sk|api|token|secret)[_-]?[A-Za-z0-9]{12,}\b")


def redact_sensitive_text(value: str, *, max_chars: int = 2000) -> str:
    """Best-effort boundary redaction; callers should still avoid raw messages."""
    if not isinstance(value, str):
        raise TypeError("redaction input must be text")
    text = _HEADER_RE.sub(lambda match: f"{match.group(1)}: <redacted>", value)
    text = _BEARER_RE.sub(lambda match: f"{match.group(1)} <redacted>", text)
    text = _TOKENISH_RE.sub("<redacted>", text)
    if len(text) > max_chars:
        text = text[:max_chars] + "...[truncated]"
    return text


def safe_error(exc: BaseException, *, code: str) -> SecurityBoundaryError:
    """Convert an arbitrary exception to a public-safe structured failure."""
    return SecurityBoundaryError(code, error_type=type(exc).__name__)
