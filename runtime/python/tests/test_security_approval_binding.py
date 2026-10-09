from __future__ import annotations

import time
import unittest
from copy import deepcopy

from manager_runtime.tools import ToolRegistry, execute_tool_request


class RecordingTool:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def execute(self, arguments: dict):
        self.calls.append(deepcopy(arguments))
        return {"accepted": arguments["value"]}

    def verify(self, arguments: dict, output) -> bool:
        return output == {"accepted": arguments["value"]}


def task() -> dict:
    return {
        "task_id": "security-approval-binding",
        "objective": "Exercise a synthetic destructive action.",
        "classification": {
            "materiality": "routine",
            "consequence": "critical",
            "uncertainty": "low",
            "reversibility": "irreversible",
            "sensitivity": "sensitive",
        },
    }


def request() -> dict:
    return {
        "request_id": "request:security-approval-binding",
        "run_id": "run:security-approval-binding",
        "tool_name": "destroy_synthetic",
        "arguments": {"value": "synthetic"},
        "target": "project:7",
        "proposed_by": "model",
        "proposal_ref": "proposal:security-approval-binding",
    }


def definition() -> dict:
    return {
        "name": "destroy_synthetic",
        "version": "1",
        "description": "Synthetic destructive tool for security regression tests.",
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


def identity() -> dict:
    now = time.time()
    return {
        "subject": "synthetic-worker",
        "issuer": "https://issuer.example",
        "audiences": ["manager-api"],
        "principal_type": "worker",
        "capabilities": ["tools.destroy"],
        "expires_at": now + 3600,
        "not_before": now - 60,
        "issued_at": now - 60,
        "token_id": "synthetic-token",
        "algorithm": "HS256",
    }


def policy(*, resources: list[str]) -> dict:
    return {
        "revision": "r1",
        "allowed_principal_types": ["worker"],
        "rules": [
            {
                "capability": "tools.destroy",
                "actions": ["tool:destroy_synthetic"],
                "resources": resources,
                "environments": ["prod"],
                "side_effect_classes": ["sensitive_destructive"],
            }
        ],
    }


def authorization(*, resources: list[str], revalidator=None, revalidator_revision=None) -> dict:
    context = {"principal": identity(), "environment": "prod"}
    if revalidator is not None:
        context["principal_revalidator"] = revalidator
    if revalidator_revision is not None:
        context["principal_revalidator_revision"] = revalidator_revision
    return {
        "security_context": context,
        "security_policy": policy(resources=resources),
        "target_verified": True,
    }


def approved(pending: dict) -> dict:
    approval = deepcopy(pending["approval"])
    approval["status"] = "approved"
    approval["approved_by"] = "synthetic-human"
    approval["resolved_by"] = "synthetic-human"
    approval["resolved_at"] = "2026-10-09T00:00:00Z"
    return approval


class SecurityApprovalBindingTests(unittest.TestCase):
    def registry(self) -> tuple[ToolRegistry, RecordingTool]:
        registry = ToolRegistry()
        tool = RecordingTool()
        registry.register(definition(), tool)
        return registry, tool

    def test_same_revision_policy_mutation_makes_approval_stale(self) -> None:
        registry, tool = self.registry()
        req = request()
        original_auth = authorization(resources=["project:7"])
        pending = execute_tool_request(task(), req, registry, original_auth)
        self.assertEqual("approval_required", pending["status"])
        approval = approved(pending)

        widened_auth = authorization(resources=["project:*"])
        widened_auth["approval"] = approval
        stale = execute_tool_request(task(), req, registry, widened_auth)

        self.assertEqual("approval_required", stale["status"])
        self.assertEqual("stale", stale["approval"]["status"])
        self.assertEqual([], tool.calls)
        self.assertNotEqual(
            approval["extensions"]["security"]["authorization_binding"],
            stale["approval"]["extensions"]["security"]["authorization_binding"],
        )

    def test_removing_live_principal_revalidation_makes_approval_stale(self) -> None:
        registry, tool = self.registry()
        req = request()
        with_revalidation = authorization(
            resources=["project:7"],
            revalidator=lambda principal: principal["subject"] == "synthetic-worker",
            revalidator_revision="revocations:v1",
        )
        pending = execute_tool_request(task(), req, registry, with_revalidation)
        self.assertEqual("approval_required", pending["status"])
        approval = approved(pending)

        without_revalidation = authorization(resources=["project:7"])
        without_revalidation["approval"] = approval
        stale = execute_tool_request(task(), req, registry, without_revalidation)

        self.assertEqual("approval_required", stale["status"])
        self.assertEqual("stale", stale["approval"]["status"])
        self.assertEqual([], tool.calls)

    def test_revalidator_revision_change_makes_approval_stale(self) -> None:
        registry, tool = self.registry()
        req = request()
        first = authorization(
            resources=["project:7"],
            revalidator=lambda principal: True,
            revalidator_revision="revocations:v1",
        )
        pending = execute_tool_request(task(), req, registry, first)
        approval = approved(pending)

        changed = authorization(
            resources=["project:7"],
            revalidator=lambda principal: True,
            revalidator_revision="revocations:v2",
        )
        changed["approval"] = approval
        stale = execute_tool_request(task(), req, registry, changed)

        self.assertEqual("approval_required", stale["status"])
        self.assertEqual("stale", stale["approval"]["status"])
        self.assertEqual([], tool.calls)

    def test_fresh_approval_under_unchanged_policy_executes(self) -> None:
        registry, tool = self.registry()
        req = request()
        auth = authorization(resources=["project:7"])
        pending = execute_tool_request(task(), req, registry, auth)
        approval = approved(pending)
        auth["approval"] = approval

        executed = execute_tool_request(task(), req, registry, auth)

        self.assertEqual("executed", executed["status"])
        self.assertEqual([{"value": "synthetic"}], tool.calls)

    def test_orphaned_revalidator_revision_fails_closed(self) -> None:
        registry, tool = self.registry()
        auth = authorization(resources=["project:7"], revalidator_revision="revocations:v1")

        blocked = execute_tool_request(task(), request(), registry, auth)

        self.assertEqual("blocked", blocked["status"])
        self.assertEqual("invalid_security_context", blocked["decision_reason"])
        self.assertEqual("security_principal_revalidator_revision_orphaned", blocked["error"])
        self.assertEqual([], tool.calls)


if __name__ == "__main__":
    unittest.main()
