"""Vendor-neutral deployment helpers for the Manager reference runtime.

These helpers deliberately avoid owning provider credentials, network identity, or
service API semantics. They provide the deployment boundary that an embedding
service can consume: validated configuration, health state, shutdown signalling,
and verified SQLite backup/restore operations.
"""

from .config import ConfigError, DeploymentConfig, load_deployment_config, validate_runtime_paths
from .health import DependencyCheck, HealthRegistry
from .lifecycle import GracefulShutdown
from .sqlite_ops import (
    BackupError,
    create_sqlite_backup,
    restore_sqlite_backup,
    verify_sqlite_backup,
)

__all__ = [
    "BackupError",
    "ConfigError",
    "DependencyCheck",
    "DeploymentConfig",
    "GracefulShutdown",
    "HealthRegistry",
    "create_sqlite_backup",
    "load_deployment_config",
    "restore_sqlite_backup",
    "validate_runtime_paths",
    "verify_sqlite_backup",
]
