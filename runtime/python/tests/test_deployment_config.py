from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from manager_runtime.deployment.config import ConfigError, load_deployment_config, validate_runtime_paths


class DeploymentConfigTests(unittest.TestCase):
    def _production_env(self, root: Path) -> dict[str, str]:
        data = root / "data"
        tmp = root / "tmp"
        secrets = root / "secrets"
        data.mkdir()
        tmp.mkdir()
        secrets.mkdir()
        return {
            "MANAGER_DEPLOY_ENV": "production",
            "MANAGER_DEPLOY_BIND_HOST": "0.0.0.0",
            "MANAGER_DEPLOY_BIND_PORT": "8080",
            "MANAGER_DEPLOY_STATE_BACKEND": "sqlite",
            "MANAGER_DEPLOY_SQLITE_PATH": str(data / "manager.sqlite3"),
            "MANAGER_DEPLOY_INSTANCE_COUNT": "1",
            "MANAGER_DEPLOY_MAX_CONCURRENCY": "8",
            "MANAGER_DEPLOY_QUEUE_LIMIT": "64",
            "MANAGER_DEPLOY_REQUEST_TIMEOUT_SECONDS": "60",
            "MANAGER_DEPLOY_PROVIDER_TIMEOUT_SECONDS": "45",
            "MANAGER_DEPLOY_MCP_TIMEOUT_SECONDS": "30",
            "MANAGER_DEPLOY_GRACEFUL_SHUTDOWN_SECONDS": "20",
            "MANAGER_DEPLOY_TLS_MODE": "external",
            "MANAGER_DEPLOY_TELEMETRY_MODE": "json_stdout",
            "MANAGER_DEPLOY_DATA_DIR": str(data),
            "MANAGER_DEPLOY_TMP_DIR": str(tmp),
            "MANAGER_DEPLOY_SECRETS_DIR": str(secrets),
            "MANAGER_DEPLOY_READ_ONLY_ROOT": "true",
        }

    def test_development_defaults_are_local_and_ephemeral(self) -> None:
        config = load_deployment_config(environ={})
        self.assertEqual(config.environment, "development")
        self.assertEqual(config.bind_host, "127.0.0.1")
        self.assertEqual(config.state_backend, "memory")
        self.assertEqual(config.tls_mode, "off")

    def test_production_refuses_inherited_development_defaults(self) -> None:
        with self.assertRaisesRegex(ConfigError, "requires explicit deployment settings"):
            load_deployment_config(environ={"MANAGER_DEPLOY_ENV": "production"})

    def test_production_single_instance_sqlite_config_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = self._production_env(Path(tmp))
            config = load_deployment_config(environ=env)
            validate_runtime_paths(config)
            self.assertEqual(config.state_backend, "sqlite")
            self.assertEqual(config.instance_count, 1)
            self.assertTrue(config.read_only_root)

    def test_production_refuses_multiple_sqlite_instances(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = self._production_env(Path(tmp))
            env["MANAGER_DEPLOY_INSTANCE_COUNT"] = "2"
            with self.assertRaisesRegex(ConfigError, "exactly one service instance"):
                load_deployment_config(environ=env)

    def test_production_refuses_memory_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = self._production_env(Path(tmp))
            env["MANAGER_DEPLOY_STATE_BACKEND"] = "memory"
            env.pop("MANAGER_DEPLOY_SQLITE_PATH")
            with self.assertRaisesRegex(ConfigError, "must not use the in-memory"):
                load_deployment_config(environ=env)

    def test_unknown_deployment_environment_setting_fails(self) -> None:
        with self.assertRaisesRegex(ConfigError, "unknown deployment environment settings"):
            load_deployment_config(environ={"MANAGER_DEPLOY_MAGIC": "1"})

    def test_direct_tls_requires_both_file_references(self) -> None:
        with self.assertRaisesRegex(ConfigError, "direct TLS requires"):
            load_deployment_config(
                environ={
                    "MANAGER_DEPLOY_TLS_MODE": "direct",
                    "MANAGER_DEPLOY_TLS_CERT_FILE": "/run/secrets/tls.crt",
                }
            )

    def test_json_config_rejects_unknown_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"environment": "development", "surprise": True}))
            with self.assertRaisesRegex(ConfigError, "unknown deployment config fields"):
                load_deployment_config(environ={}, config_file=path)

    def test_path_validation_rejects_missing_directory(self) -> None:
        config = load_deployment_config(
            environ={
                "MANAGER_DEPLOY_DATA_DIR": "/definitely/missing/manager-data",
                "MANAGER_DEPLOY_TMP_DIR": "/definitely/missing/manager-tmp",
            }
        )
        with self.assertRaises(ConfigError):
            validate_runtime_paths(config)
