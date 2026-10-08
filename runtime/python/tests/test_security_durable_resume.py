from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from manager_runtime.state.approvals import (
    checkpoint_pending_tool_approval,
    resume_tool_approval,
)
from manager_runtime.state.base import RunStateError
from manager_runtime.state.sqlite_store import SQLiteRunStore
from manager_runtime.tools import ToolRegistry, execute_tool_request


class RecordingTool:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def execute(self, arguments: dict):
        self.calls.append(dict(arguments))
        return {"ok": True}

    def verify(self, arguments: dict, output) -> bool:
        return True


def identity(*, subject: str = "worker-1", caps=("tools.write",), exp=None) -> dict:
    now = time.time()
    return {
        "subject": subject,
        "issuer": "synthetic-issuer",
        "audiences": ["manager"],
        "principal_type": "worker",
        "capabilities": list(caps),
        "expires_at": now + 300 if exp is None else exp,
        "not_before": now - 5,
        "issued_at": now - 10,
        "token_id": "synthetic-token-id",
        "algorithm": "HS256",
    }


def policy(revision: str = "r1") -> dict:
    return {
        "revision": revision,
        "allowed_principal_types": ["worker"],
        "rules": [
            {
                "capability": "tools.write",
                "actions": ["tool:update_project"],
                "resources": ["project:*"],
                "environments": ["prod"],
                "side_effect_classes": ["reversible_write"],
            }
        ],
    }


def authorization(*, principal=None, revision: str = "r1", marker: str | None = None) -> dict:
    context = {
        "principal": principal or identity(),
        "environment": "prod",
    }
    if marker is not None:
        context["raw_token"] = marker
    return {
        "require_security_context": True,
        "security_context": context,
        "security_policy": policy(revision),
    }


def task() -> dict:
    return {
        "task_id": "security-durable",
        "objective": "Synthetic durable authorization test.",
        "classification": {
            "materiality": "material",
            "consequence": "medium",
            "uncertainty": "low",
            "reversibility": "reversible",
            "sensitivity": "public",
        },
    }


def request() -> dict:
    return {
        "request_id": "request:security-durable",
        "run_id": "run:security-durable",
        "tool_name": "update_project",
        "target": "project:7",
        "arguments": {},
        "proposed_by": "system",
    }


def decision(approval: dict) -> dict:
    return {
        "approval_id": approval["approval_id"],
        "decision": "approved",
        "decided_by": "synthetic-human",
        "decided_at": "2026-10-08T14:30:00Z",
    }


class DurableSecurityResumeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = ToolRegistry()
        self.tool = RecordingTool()
        self.registry.register(
            {
                "name": "update_project",
                "version": "1",
                "description": "Synthetic material update.",
                "side_effect_class": "reversible_write",
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                "requires_verification": True,
                "sensitive_output": False,
            },
            self.tool,
        )

    def _checkpoint(self, store: SQLiteRunStore, auth: dict) -> dict:
        result = execute_tool_request(task(), request(), self.registry, auth)
        self.assertEqual("approval_required", result["status"])
        return checkpoint_pending_tool_approval(
            task(),
            request(),
            result,
            self.registry,
            store,
            authorization_context=auth,
        )

    def test_restart_requires_fresh_strict_authorization_and_then_executes(self) -> None:
        principal = identity()
        current = authorization(principal=principal)
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteRunStore(Path(directory) / "state.sqlite3")
            state = self._checkpoint(store, current)
            resumed = resume_tool_approval(
                store,
                request()["run_id"],
                self.registry,
                decision(state["pending_action"]["approval"]),
                current_authorization=current,
            )

        self.assertEqual("completed", resumed["status"])
        self.assertEqual([{}], self.tool.calls)

    def test_durable_checkpoint_does_not_persist_raw_identity_or_credential_context(self) -> None:
        marker = "synthetic-secret-marker-never-persist"
        current = authorization(marker=marker)
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteRunStore(Path(directory) / "state.sqlite3")
            state = self._checkpoint(store, current)
            rendered = json.dumps(state, sort_keys=True, default=str)

        self.assertNotIn(marker, rendered)
        self.assertNotIn("security_context", rendered)
        self.assertNotIn("security_policy", rendered)
        security_extension = state["pending_action"]["approval"]["extensions"]["security"]
        self.assertTrue(security_extension["authorization_binding"].startswith("sha256:"))

    def test_policy_change_after_restart_makes_approval_stale(self) -> None:
        principal = identity()
        initial = authorization(principal=principal, revision="r1")
        current = authorization(principal=principal, revision="r2")
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteRunStore(Path(directory) / "state.sqlite3")
            state = self._checkpoint(store, initial)
            resumed = resume_tool_approval(
                store,
                request()["run_id"],
                self.registry,
                decision(state["pending_action"]["approval"]),
                current_authorization=current,
            )

        self.assertEqual("waiting_approval", resumed["status"])
        self.assertEqual("stale", resumed["pending_action"]["approval"]["status"])
        self.assertEqual([], self.tool.calls)

    def test_principal_change_after_restart_makes_approval_stale(self) -> None:
        initial = authorization(principal=identity(subject="worker-1"))
        current = authorization(principal=identity(subject="worker-2"))
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteRunStore(Path(directory) / "state.sqlite3")
            state = self._checkpoint(store, initial)
            resumed = resume_tool_approval(
                store,
                request()["run_id"],
                self.registry,
                decision(state["pending_action"]["approval"]),
                current_authorization=current,
            )

        self.assertEqual("waiting_approval", resumed["status"])
        self.assertEqual("stale", resumed["pending_action"]["approval"]["status"])
        self.assertEqual([], self.tool.calls)

    def test_denied_capability_after_restart_blocks_before_state_or_side_effect(self) -> None:
        initial = authorization()
        current = authorization(principal=identity(caps=("tools.read",)))
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteRunStore(Path(directory) / "state.sqlite3")
            state = self._checkpoint(store, initial)
            revision = state["revision"]
            with self.assertRaisesRegex(
                RunStateError, "current security authorization is required"
            ):
                resume_tool_approval(
                    store,
                    request()["run_id"],
                    self.registry,
                    decision(state["pending_action"]["approval"]),
                    current_authorization=current,
                )
            persisted = store.load(request()["run_id"])

        self.assertEqual("waiting_approval", persisted["status"])
        self.assertEqual(revision, persisted["revision"])
        self.assertEqual([], self.tool.calls)

    def test_expired_identity_after_restart_blocks_before_side_effect(self) -> None:
        initial = authorization()
        current = authorization(principal=identity(exp=time.time() - 120))
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteRunStore(Path(directory) / "state.sqlite3")
            state = self._checkpoint(store, initial)
            with self.assertRaisesRegex(RunStateError, "security_identity_expired"):
                resume_tool_approval(
                    store,
                    request()["run_id"],
                    self.registry,
                    decision(state["pending_action"]["approval"]),
                    current_authorization=current,
                )

        self.assertEqual([], self.tool.calls)


if __name__ == "__main__":
    unittest.main()
