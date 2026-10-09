from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from manager_runtime.deployment.config import DeploymentConfig
from manager_runtime.service import create_gateway_server
from manager_runtime.service.api import JsonLimits
from manager_runtime.service.gateway import GatewayConfigError, GatewaySettings


class _Backend:
    def readiness(self):
        return True

    def submit(self, task_input, *, subject):
        raise AssertionError("not used")

    def get_run(self, run_id, *, subject):
        raise AssertionError("not used")

    def resume(self, run_id, *, subject):
        raise AssertionError("not used")

    def cancel(self, run_id, *, subject):
        raise AssertionError("not used")

    def decide_approval(self, run_id, decision, *, subject):
        raise AssertionError("not used")

    def resolve_recovery(self, run_id, resolution, *, subject):
        raise AssertionError("not used")


def _production(directory: str) -> DeploymentConfig:
    return DeploymentConfig(
        environment="production",
        bind_host="127.0.0.1",
        bind_port=0,
        state_backend="sqlite",
        sqlite_path=str(Path(directory) / "state.sqlite3"),
        instance_count=1,
        max_concurrency=1,
        queue_limit=1,
        request_timeout_seconds=1,
        provider_timeout_seconds=1,
        mcp_timeout_seconds=1,
        graceful_shutdown_seconds=1,
        tls_mode="external",
        tls_cert_file=None,
        tls_key_file=None,
        telemetry_mode="json_stdout",
        data_dir=directory,
        tmp_dir=directory,
        secrets_dir=directory,
        read_only_root=True,
    )


def _settings(*, auth: str, path: str) -> GatewaySettings:
    return GatewaySettings(
        auth_mode=auth,
        auth_secret_name="ci-credential",
        backend_factory=None,
        idempotency_path=path,
        max_request_bytes=4096,
        max_header_bytes=4096,
        json_limits=JsonLimits(max_depth=8, max_nodes=512, max_string_chars=4096),
    )


class DirectGatewayConfigTests(unittest.TestCase):
    def test_direct_production_construction_cannot_disable_authentication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(GatewayConfigError, "bearer"):
                create_gateway_server(
                    _production(directory),
                    _settings(
                        auth="none",
                        path=str(Path(directory) / "api.sqlite3"),
                    ),
                    _Backend(),
                )

    def test_direct_production_construction_requires_absolute_idempotency_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "ci-credential").write_text("placeholder\n", encoding="utf-8")
            with self.assertRaisesRegex(GatewayConfigError, "absolute"):
                create_gateway_server(
                    _production(directory),
                    _settings(auth="bearer", path="relative.sqlite3"),
                    _Backend(),
                )


if __name__ == "__main__":
    unittest.main()
