from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping


class ConfigError(ValueError):
    """Raised when deployment configuration is invalid or unsafe."""


_PREFIX = "MANAGER_DEPLOY_"
_ENV_TO_FIELD = {
    f"{_PREFIX}ENV": "environment",
    f"{_PREFIX}CONFIG_FILE": "config_file",
    f"{_PREFIX}BIND_HOST": "bind_host",
    f"{_PREFIX}BIND_PORT": "bind_port",
    f"{_PREFIX}STATE_BACKEND": "state_backend",
    f"{_PREFIX}SQLITE_PATH": "sqlite_path",
    f"{_PREFIX}INSTANCE_COUNT": "instance_count",
    f"{_PREFIX}MAX_CONCURRENCY": "max_concurrency",
    f"{_PREFIX}QUEUE_LIMIT": "queue_limit",
    f"{_PREFIX}REQUEST_TIMEOUT_SECONDS": "request_timeout_seconds",
    f"{_PREFIX}PROVIDER_TIMEOUT_SECONDS": "provider_timeout_seconds",
    f"{_PREFIX}MCP_TIMEOUT_SECONDS": "mcp_timeout_seconds",
    f"{_PREFIX}GRACEFUL_SHUTDOWN_SECONDS": "graceful_shutdown_seconds",
    f"{_PREFIX}TLS_MODE": "tls_mode",
    f"{_PREFIX}TLS_CERT_FILE": "tls_cert_file",
    f"{_PREFIX}TLS_KEY_FILE": "tls_key_file",
    f"{_PREFIX}TELEMETRY_MODE": "telemetry_mode",
    f"{_PREFIX}DATA_DIR": "data_dir",
    f"{_PREFIX}TMP_DIR": "tmp_dir",
    f"{_PREFIX}SECRETS_DIR": "secrets_dir",
    f"{_PREFIX}READ_ONLY_ROOT": "read_only_root",
}

_ALLOWED_FIELDS = {
    "environment",
    "bind_host",
    "bind_port",
    "state_backend",
    "sqlite_path",
    "instance_count",
    "max_concurrency",
    "queue_limit",
    "request_timeout_seconds",
    "provider_timeout_seconds",
    "mcp_timeout_seconds",
    "graceful_shutdown_seconds",
    "tls_mode",
    "tls_cert_file",
    "tls_key_file",
    "telemetry_mode",
    "data_dir",
    "tmp_dir",
    "secrets_dir",
    "read_only_root",
}

_PRODUCTION_REQUIRED = {
    "bind_host",
    "bind_port",
    "state_backend",
    "instance_count",
    "max_concurrency",
    "queue_limit",
    "request_timeout_seconds",
    "provider_timeout_seconds",
    "mcp_timeout_seconds",
    "graceful_shutdown_seconds",
    "tls_mode",
    "telemetry_mode",
    "data_dir",
    "tmp_dir",
    "read_only_root",
}

_DEFAULTS: dict[str, object] = {
    "environment": "development",
    "bind_host": "127.0.0.1",
    "bind_port": 8080,
    "state_backend": "memory",
    "sqlite_path": None,
    "instance_count": 1,
    "max_concurrency": 4,
    "queue_limit": 32,
    "request_timeout_seconds": 60.0,
    "provider_timeout_seconds": 45.0,
    "mcp_timeout_seconds": 30.0,
    "graceful_shutdown_seconds": 20.0,
    "tls_mode": "off",
    "tls_cert_file": None,
    "tls_key_file": None,
    "telemetry_mode": "off",
    "data_dir": "./var/manager",
    "tmp_dir": "/tmp/manager",
    "secrets_dir": None,
    "read_only_root": False,
}


@dataclass(frozen=True, slots=True)
class DeploymentConfig:
    environment: str
    bind_host: str
    bind_port: int
    state_backend: str
    sqlite_path: str | None
    instance_count: int
    max_concurrency: int
    queue_limit: int
    request_timeout_seconds: float
    provider_timeout_seconds: float
    mcp_timeout_seconds: float
    graceful_shutdown_seconds: float
    tls_mode: str
    tls_cert_file: str | None
    tls_key_file: str | None
    telemetry_mode: str
    data_dir: str
    tmp_dir: str
    secrets_dir: str | None
    read_only_root: bool

    def effective_dict(self) -> dict[str, object]:
        """Return safe-to-log deployment settings.

        The deployment config contains references to secret locations, never secret
        values. Paths are kept because operators need them for diagnostics.
        """

        return asdict(self)


def _parse_bool(value: object, *, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise ConfigError(f"{field} must be a boolean")


def _parse_int(value: object, *, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise ConfigError(f"{field} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{field} must be an integer") from exc
    if not minimum <= parsed <= maximum:
        raise ConfigError(f"{field} must be between {minimum} and {maximum}")
    return parsed


def _parse_float(value: object, *, field: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool):
        raise ConfigError(f"{field} must be a number")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{field} must be a number") from exc
    if not minimum <= parsed <= maximum:
        raise ConfigError(f"{field} must be between {minimum} and {maximum}")
    return parsed


def _optional_string(value: object, *, field: str) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ConfigError(f"{field} must be a string")
    stripped = value.strip()
    if not stripped:
        return None
    if "\x00" in stripped:
        raise ConfigError(f"{field} must not contain NUL bytes")
    return stripped


def _required_string(value: object, *, field: str) -> str:
    parsed = _optional_string(value, field=field)
    if parsed is None:
        raise ConfigError(f"{field} must not be empty")
    return parsed


def _read_config_file(path: str | Path) -> dict[str, object]:
    file_path = Path(path)
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"deployment config file does not exist: {file_path}") from exc
    except OSError as exc:
        raise ConfigError(f"cannot read deployment config file: {file_path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"deployment config file is not valid JSON: {file_path}") from exc
    if not isinstance(payload, dict):
        raise ConfigError("deployment config file must contain a JSON object")
    unknown = sorted(set(payload) - _ALLOWED_FIELDS)
    if unknown:
        raise ConfigError(f"unknown deployment config fields: {', '.join(unknown)}")
    return dict(payload)


def _collect_environment(environ: Mapping[str, str]) -> tuple[dict[str, object], set[str], str | None]:
    unknown = sorted(
        key for key in environ if key.startswith(_PREFIX) and key not in _ENV_TO_FIELD
    )
    if unknown:
        raise ConfigError(f"unknown deployment environment settings: {', '.join(unknown)}")

    values: dict[str, object] = {}
    provided: set[str] = set()
    config_file: str | None = None
    for env_name, field in _ENV_TO_FIELD.items():
        if env_name not in environ:
            continue
        if field == "config_file":
            config_file = environ[env_name]
            continue
        values[field] = environ[env_name]
        provided.add(field)
    return values, provided, config_file


def load_deployment_config(
    *,
    environ: Mapping[str, str] | None = None,
    config_file: str | Path | None = None,
) -> DeploymentConfig:
    """Load deployment settings from JSON plus ``MANAGER_DEPLOY_*`` overrides.

    File values are loaded first, environment values override them, and unknown
    deployment-prefixed settings fail closed. Staging and production require the
    operationally important fields to be explicitly supplied rather than inherited
    from development defaults.
    """

    env = os.environ if environ is None else environ
    env_values, env_provided, env_config_file = _collect_environment(env)
    selected_config_file = config_file if config_file is not None else env_config_file

    file_values: dict[str, object] = {}
    file_provided: set[str] = set()
    if selected_config_file:
        file_values = _read_config_file(selected_config_file)
        file_provided = set(file_values)

    raw = dict(_DEFAULTS)
    raw.update(file_values)
    raw.update(env_values)
    provided = file_provided | env_provided

    environment = _required_string(raw["environment"], field="environment").lower()
    if environment not in {"development", "testing", "staging", "production"}:
        raise ConfigError("environment must be development, testing, staging, or production")

    if environment in {"staging", "production"}:
        missing = sorted(_PRODUCTION_REQUIRED - provided)
        if missing:
            raise ConfigError(
                f"{environment} requires explicit deployment settings: {', '.join(missing)}"
            )

    bind_host = _required_string(raw["bind_host"], field="bind_host")
    bind_port = _parse_int(raw["bind_port"], field="bind_port", minimum=1, maximum=65535)
    state_backend = _required_string(raw["state_backend"], field="state_backend").lower()
    if state_backend not in {"memory", "sqlite"}:
        raise ConfigError("state_backend must be memory or sqlite")

    sqlite_path = _optional_string(raw.get("sqlite_path"), field="sqlite_path")
    instance_count = _parse_int(raw["instance_count"], field="instance_count", minimum=1, maximum=1024)
    max_concurrency = _parse_int(raw["max_concurrency"], field="max_concurrency", minimum=1, maximum=100000)
    queue_limit = _parse_int(raw["queue_limit"], field="queue_limit", minimum=0, maximum=1000000)
    request_timeout_seconds = _parse_float(
        raw["request_timeout_seconds"], field="request_timeout_seconds", minimum=0.1, maximum=86400.0
    )
    provider_timeout_seconds = _parse_float(
        raw["provider_timeout_seconds"], field="provider_timeout_seconds", minimum=0.1, maximum=86400.0
    )
    mcp_timeout_seconds = _parse_float(
        raw["mcp_timeout_seconds"], field="mcp_timeout_seconds", minimum=0.1, maximum=86400.0
    )
    graceful_shutdown_seconds = _parse_float(
        raw["graceful_shutdown_seconds"], field="graceful_shutdown_seconds", minimum=0.1, maximum=3600.0
    )

    tls_mode = _required_string(raw["tls_mode"], field="tls_mode").lower()
    if tls_mode not in {"off", "direct", "external"}:
        raise ConfigError("tls_mode must be off, direct, or external")
    tls_cert_file = _optional_string(raw.get("tls_cert_file"), field="tls_cert_file")
    tls_key_file = _optional_string(raw.get("tls_key_file"), field="tls_key_file")
    if tls_mode == "direct" and (tls_cert_file is None or tls_key_file is None):
        raise ConfigError("direct TLS requires tls_cert_file and tls_key_file")
    if tls_mode != "direct" and (tls_cert_file is not None or tls_key_file is not None):
        raise ConfigError("TLS certificate/key paths are valid only when tls_mode=direct")

    telemetry_mode = _required_string(raw["telemetry_mode"], field="telemetry_mode").lower()
    if telemetry_mode not in {"off", "json_stdout"}:
        raise ConfigError("telemetry_mode must be off or json_stdout")

    data_dir = _required_string(raw["data_dir"], field="data_dir")
    tmp_dir = _required_string(raw["tmp_dir"], field="tmp_dir")
    secrets_dir = _optional_string(raw.get("secrets_dir"), field="secrets_dir")
    read_only_root = _parse_bool(raw["read_only_root"], field="read_only_root")

    if state_backend == "sqlite":
        if sqlite_path is None:
            raise ConfigError("sqlite state_backend requires sqlite_path")
        if instance_count != 1:
            raise ConfigError("SQLite reference state supports exactly one service instance")
    elif sqlite_path is not None:
        raise ConfigError("sqlite_path may be set only when state_backend=sqlite")

    if environment in {"staging", "production"}:
        if state_backend == "memory":
            raise ConfigError(f"{environment} must not use the in-memory state backend")
        if tls_mode == "off":
            raise ConfigError(f"{environment} requires direct or externally terminated TLS")
        if telemetry_mode == "off":
            raise ConfigError(f"{environment} requires operational telemetry")
        if not read_only_root:
            raise ConfigError(f"{environment} requires read_only_root=true for the reference container")
        for field, value in {
            "data_dir": data_dir,
            "tmp_dir": tmp_dir,
            "sqlite_path": sqlite_path or "",
        }.items():
            if not Path(value).is_absolute():
                raise ConfigError(f"{field} must be an absolute path in {environment}")

    if queue_limit and queue_limit < max_concurrency:
        raise ConfigError("queue_limit must be zero (disabled) or at least max_concurrency")
    if graceful_shutdown_seconds > request_timeout_seconds:
        # A drain window longer than the request ceiling usually means the two limits
        # were configured independently by mistake. It is safe to reject and force an
        # operator to make the intended relationship explicit.
        raise ConfigError("graceful_shutdown_seconds must not exceed request_timeout_seconds")

    return DeploymentConfig(
        environment=environment,
        bind_host=bind_host,
        bind_port=bind_port,
        state_backend=state_backend,
        sqlite_path=sqlite_path,
        instance_count=instance_count,
        max_concurrency=max_concurrency,
        queue_limit=queue_limit,
        request_timeout_seconds=request_timeout_seconds,
        provider_timeout_seconds=provider_timeout_seconds,
        mcp_timeout_seconds=mcp_timeout_seconds,
        graceful_shutdown_seconds=graceful_shutdown_seconds,
        tls_mode=tls_mode,
        tls_cert_file=tls_cert_file,
        tls_key_file=tls_key_file,
        telemetry_mode=telemetry_mode,
        data_dir=data_dir,
        tmp_dir=tmp_dir,
        secrets_dir=secrets_dir,
        read_only_root=read_only_root,
    )


def _probe_writable_directory(path: Path, *, label: str) -> None:
    if not path.exists():
        raise ConfigError(f"{label} does not exist: {path}")
    if not path.is_dir():
        raise ConfigError(f"{label} is not a directory: {path}")
    try:
        with tempfile.NamedTemporaryFile(prefix=".manager-write-probe-", dir=path, delete=True):
            pass
    except OSError as exc:
        raise ConfigError(f"{label} is not writable: {path}") from exc


def validate_runtime_paths(config: DeploymentConfig) -> None:
    """Validate filesystem assumptions before the service accepts work."""

    data_dir = Path(config.data_dir)
    tmp_dir = Path(config.tmp_dir)
    _probe_writable_directory(data_dir, label="data_dir")
    _probe_writable_directory(tmp_dir, label="tmp_dir")

    if config.secrets_dir is not None:
        secrets_dir = Path(config.secrets_dir)
        if not secrets_dir.exists() or not secrets_dir.is_dir():
            raise ConfigError(f"secrets_dir is not an existing directory: {secrets_dir}")

    if config.state_backend == "sqlite":
        assert config.sqlite_path is not None
        sqlite_path = Path(config.sqlite_path)
        _probe_writable_directory(sqlite_path.parent, label="sqlite parent directory")
        if sqlite_path.exists() and not sqlite_path.is_file():
            raise ConfigError(f"sqlite_path is not a regular file: {sqlite_path}")
