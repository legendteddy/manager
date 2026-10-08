from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import time
import unittest
from copy import deepcopy
from pathlib import Path

from manager_runtime.state import (
    SQLITE_STATE_SCHEMA_VERSION,
    OperationConflict,
    RunLeaseConflict,
    RunLeaseExpired,
    RunStateError,
    SQLiteRunStore,
    checkpoint_pending_tool_approval,
    resume_tool_approval,
)
from manager_runtime.tools import ToolRegistry, execute_tool_request


def _now() -> str:
    return "2026-10-08T12:00:00Z"


def _state(*, status: str = "running", revision: int = 1) -> dict:
    return {
        "run_id": "run:distributed",
        "task_id": "distributed",
        "status": status,
        "revision": revision,
        "created_at": _now(),
        "updated_at": _now(),
        "task": {
            "task_id": "distributed",
            "objective": "Perform one synthetic consequential action.",
            "classification": {
                "materiality": "routine",
                "consequence": "high",
                "uncertainty": "low",
                "reversibility": "irreversible",
                "sensitivity": "public",
            },
        },
        "pending_action": {} if status in {"waiting_approval", "executing"} else None,
        "last_tool_result": None,
        "trace_snapshot": None,
        "result_snapshot": None,
        "recovery_reason": (
            "synthetic uncertain result" if status == "recovery_required" else None
        ),
        "extensions": {},
    }


def _task() -> dict:
    return _state()["task"]


def _request() -> dict:
    return {
        "request_id": "request:distributed:commit",
        "run_id": "run:distributed",
        "tool_name": "commit",
        "arguments": {"value": "synthetic"},
        "target": "synthetic-target",
        "proposed_by": "model",
        "proposal_ref": "proposal:distributed:commit",
    }


def _definition() -> dict:
    return {
        "name": "commit",
        "version": "1",
        "description": "Synthetic consequential tool.",
        "side_effect_class": "sensitive_destructive",
        "input_schema": {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        "requires_verification": True,
        "sensitive_output": False,
    }


def _decision() -> dict:
    return {
        "approval_id": "approval:request:distributed:commit",
        "decision": "approved",
        "decided_by": "synthetic-human",
        "decided_at": "2026-10-08T12:01:00Z",
    }


class CountingTool:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def execute(self, arguments: dict):
        self.calls.append(dict(arguments))
        return {"accepted": arguments["value"]}

    def verify(self, arguments: dict, output) -> bool:
        return output.get("accepted") == arguments["value"]


class BlockingTool(CountingTool):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def execute(self, arguments: dict):
        self.calls.append(dict(arguments))
        self.started.set()
        if not self.release.wait(timeout=5):
            raise RuntimeError("synthetic blocking tool timed out")
        return {"accepted": arguments["value"]}


class EffectThenErrorTool(CountingTool):
    def execute(self, arguments: dict):
        self.calls.append(dict(arguments))
        raise RuntimeError("synthetic response lost after effect")


class CrashAfterEffectTool(CountingTool):
    def execute(self, arguments: dict):
        self.calls.append(dict(arguments))
        raise SystemExit("synthetic process interruption")


class DistributedStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "manager-state.sqlite3"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _checkpoint(self, tool: CountingTool | None = None):
        tool = tool or CountingTool()
        registry = ToolRegistry()
        registry.register(_definition(), tool)
        request = _request()
        pending = execute_tool_request(
            _task(),
            request,
            registry,
            {"scope_authorized": True, "target_verified": True},
        )
        self.assertEqual(pending["status"], "approval_required")
        store = SQLiteRunStore(self.path)
        state = checkpoint_pending_tool_approval(
            _task(),
            request,
            pending,
            registry,
            store,
            authorization_context={"scope_authorized": True, "target_verified": True},
        )
        return store, registry, tool, state

    def test_legacy_sqlite_store_migrates_transactionally_to_v2(self) -> None:
        state = _state()
        connection = sqlite3.connect(self.path)
        connection.execute(
            """
            CREATE TABLE manager_runs (
                run_id TEXT PRIMARY KEY,
                revision INTEGER NOT NULL,
                state_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO manager_runs(run_id, revision, state_json) VALUES (?, ?, ?)",
            (state["run_id"], 1, json.dumps(state, sort_keys=True, separators=(",", ":"))),
        )
        connection.commit()
        connection.close()

        store = SQLiteRunStore(self.path)
        self.assertEqual(store.load(state["run_id"])["revision"], 1)
        connection = sqlite3.connect(self.path)
        version = connection.execute(
            "SELECT value FROM manager_state_meta WHERE key = 'schema_version'"
        ).fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        connection.close()
        self.assertEqual(int(version), SQLITE_STATE_SCHEMA_VERSION)
        self.assertIn("manager_run_leases", tables)
        self.assertIn("manager_operations", tables)

    def test_future_sqlite_schema_version_fails_closed(self) -> None:
        connection = sqlite3.connect(self.path)
        connection.execute(
            "CREATE TABLE manager_runs(run_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, state_json TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE manager_state_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO manager_state_meta(key, value) VALUES ('schema_version', '999')"
        )
        connection.commit()
        connection.close()
        with self.assertRaises(RunStateError):
            SQLiteRunStore(self.path)

    def test_active_lease_is_exclusive_and_transfer_fences_stale_worker(self) -> None:
        store = SQLiteRunStore(self.path)
        store.create(_state())
        lease_a = store.acquire_lease(
            "run:distributed", "worker:a", ttl_seconds=30
        )
        with self.assertRaises(RunLeaseConflict):
            SQLiteRunStore(self.path).acquire_lease(
                "run:distributed", "worker:b", ttl_seconds=30
            )
        store.release_lease(lease_a)
        lease_b = store.acquire_lease(
            "run:distributed", "worker:b", ttl_seconds=30
        )
        self.assertGreater(lease_b.fencing_token, lease_a.fencing_token)

        candidate = _state(status="completed", revision=2)
        with self.assertRaises(RunLeaseExpired):
            store.fenced_compare_and_swap(
                "run:distributed", 1, candidate, lease=lease_a
            )
        persisted = store.fenced_compare_and_swap(
            "run:distributed", 1, candidate, lease=lease_b
        )
        self.assertEqual(persisted["status"], "completed")

    def test_expired_lease_transfers_with_higher_fence(self) -> None:
        store = SQLiteRunStore(self.path)
        store.create(_state())
        lease_a = store.acquire_lease(
            "run:distributed", "worker:a", ttl_seconds=1
        )
        time.sleep(1.1)
        lease_b = SQLiteRunStore(self.path).acquire_lease(
            "run:distributed", "worker:b", ttl_seconds=30
        )
        self.assertGreater(lease_b.fencing_token, lease_a.fencing_token)
        with self.assertRaises(RunLeaseExpired):
            store.assert_lease(lease_a)

    def test_operation_identity_is_durable_and_collision_fails_closed(self) -> None:
        store = SQLiteRunStore(self.path)
        waiting = _state(status="waiting_approval")
        store.create(waiting)
        lease = store.acquire_lease(
            "run:distributed", "worker:a", ttl_seconds=30
        )
        executing = deepcopy(waiting)
        executing["status"] = "executing"
        executing["revision"] = 2
        store.fenced_compare_and_swap(
            "run:distributed", 1, executing, lease=lease
        )

        operation = store.begin_operation(
            lease=lease,
            operation_id="operation:stable",
            request_fingerprint="sha256:synthetic",
        )
        self.assertTrue(operation.claimed)
        duplicate = store.begin_operation(
            lease=lease,
            operation_id="operation:stable",
            request_fingerprint="sha256:synthetic",
        )
        self.assertFalse(duplicate.claimed)
        with self.assertRaises(OperationConflict):
            store.begin_operation(
                lease=lease,
                operation_id="operation:stable",
                request_fingerprint="sha256:different",
            )

        result = {"status": "executed", "request_id": "operation:stable"}
        store.finish_operation(
            operation,
            lease=lease,
            status="confirmed",
            result=result,
        )
        reloaded = SQLiteRunStore(self.path).load_operation("operation:stable")
        self.assertIsNotNone(reloaded)
        self.assertEqual(reloaded.status, "confirmed")
        self.assertEqual(reloaded.result, result)

    def test_live_executor_cannot_be_concurrently_recovered_or_reexecuted(self) -> None:
        blocking = BlockingTool()
        store, registry, _tool, state = self._checkpoint(blocking)
        outcome: list[dict] = []
        errors: list[BaseException] = []

        def first_worker() -> None:
            try:
                outcome.append(
                    resume_tool_approval(
                        SQLiteRunStore(self.path),
                        state["run_id"],
                        registry,
                        _decision(),
                        current_authorization={
                            "scope_authorized": True,
                            "target_verified": True,
                        },
                        worker_id="worker:first",
                    )
                )
            except BaseException as exc:  # pragma: no cover - diagnostic capture
                errors.append(exc)

        thread = threading.Thread(target=first_worker)
        thread.start()
        self.assertTrue(blocking.started.wait(timeout=2))
        during = store.load(state["run_id"])
        self.assertEqual(during["status"], "executing")

        with self.assertRaises(RunStateError):
            resume_tool_approval(
                SQLiteRunStore(self.path, timeout_seconds=0.05),
                state["run_id"],
                registry,
                _decision(),
                current_authorization={
                    "scope_authorized": True,
                    "target_verified": True,
                },
                worker_id="worker:second",
            )
        self.assertEqual(store.load(state["run_id"])["status"], "executing")
        self.assertEqual(len(blocking.calls), 1)

        blocking.release.set()
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(outcome[0]["status"], "completed")
        self.assertEqual(len(blocking.calls), 1)

    def test_adapter_failure_after_execution_intent_requires_recovery(self) -> None:
        tool = EffectThenErrorTool()
        store, registry, _tool, state = self._checkpoint(tool)
        recovered = resume_tool_approval(
            store,
            state["run_id"],
            registry,
            _decision(),
            current_authorization={
                "scope_authorized": True,
                "target_verified": True,
            },
            worker_id="worker:error",
        )
        self.assertEqual(recovered["status"], "recovery_required")
        self.assertEqual(len(tool.calls), 1)
        operation = store.load_operation(_request()["request_id"])
        self.assertIsNotNone(operation)
        self.assertEqual(operation.status, "outcome_unknown")

        with self.assertRaises(RunStateError):
            resume_tool_approval(
                store,
                state["run_id"],
                registry,
                _decision(),
                current_authorization={
                    "scope_authorized": True,
                    "target_verified": True,
                },
                worker_id="worker:retry",
            )
        self.assertEqual(len(tool.calls), 1)

    def test_crash_after_effect_leaves_started_operation_and_never_auto_retries(self) -> None:
        tool = CrashAfterEffectTool()
        store, registry, _tool, state = self._checkpoint(tool)
        with self.assertRaises(SystemExit):
            resume_tool_approval(
                store,
                state["run_id"],
                registry,
                _decision(),
                current_authorization={
                    "scope_authorized": True,
                    "target_verified": True,
                },
                worker_id="worker:crash",
            )
        executing = store.load(state["run_id"])
        self.assertEqual(executing["status"], "executing")
        operation = store.load_operation(_request()["request_id"])
        self.assertIsNotNone(operation)
        self.assertEqual(operation.status, "started")
        self.assertEqual(len(tool.calls), 1)

        recovered = resume_tool_approval(
            store,
            state["run_id"],
            registry,
            _decision(),
            current_authorization={
                "scope_authorized": True,
                "target_verified": True,
            },
            worker_id="worker:recovery",
        )
        self.assertEqual(recovered["status"], "recovery_required")
        self.assertEqual(len(tool.calls), 1)

    def test_confirmed_operation_is_reused_without_second_tool_call(self) -> None:
        tool = CountingTool()
        store, registry, _tool, state = self._checkpoint(tool)
        completed = resume_tool_approval(
            store,
            state["run_id"],
            registry,
            _decision(),
            current_authorization={
                "scope_authorized": True,
                "target_verified": True,
            },
            worker_id="worker:first",
        )
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(len(tool.calls), 1)
        operation = store.load_operation(_request()["request_id"])
        self.assertEqual(operation.status, "confirmed")

        with self.assertRaises(RunStateError):
            resume_tool_approval(
                store,
                state["run_id"],
                registry,
                _decision(),
                current_authorization={
                    "scope_authorized": True,
                    "target_verified": True,
                },
                worker_id="worker:duplicate-client",
            )
        self.assertEqual(len(tool.calls), 1)


if __name__ == "__main__":
    unittest.main()
