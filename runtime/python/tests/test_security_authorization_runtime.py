from __future__ import annotations

import time
import unittest

from manager_runtime.tools.base import ToolRegistry
from manager_runtime.tools.runtime import execute_tool_request


class Adapter:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, arguments: dict):
        self.calls += 1
        return {"ok": True}

    def verify(self, arguments: dict, output) -> bool:
        return True


def identity(*, caps=("tools.write",), subject="worker-1", exp=None):
    now = time.time()
    return {
        "subject": subject,
        "issuer": "issuer",
        "audiences": ["manager"],
        "principal_type": "worker",
        "capabilities": list(caps),
        "expires_at": exp if exp is not None else now + 300,
        "not_before": now - 5,
        "issued_at": now - 10,
        "token_id": "j1",
        "algorithm": "HS256",
    }


def policy(revision="r1", capability="tools.write"):
    return {
        "revision": revision,
        "rules": [
            {
                "capability": capability,
                "actions": ["tool:update_project"],
                "resources": ["project:*"],
                "environments": ["prod"],
                "side_effect_classes": ["reversible_write"],
            }
        ],
    }


def auth(revision="r1", principal=None, principal_revalidator=None):
    context = {
        "principal": principal or identity(),
        "environment": "prod",
    }
    if principal_revalidator is not None:
        context["principal_revalidator"] = principal_revalidator
    return {
        "require_security_context": True,
        "security_context": context,
        "security_policy": policy(revision),
    }


def request():
    return {
        "request_id": "req1",
        "run_id": "run1",
        "tool_name": "update_project",
        "target": "project:7",
        "arguments": {},
        "proposed_by": "system",
    }


def task():
    return {"classification": {"materiality": "material"}}


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.registry = ToolRegistry()
        self.adapter = Adapter()
        self.registry.register(
            {
                "name": "update_project",
                "description": "Synthetic update tool.",
                "version": "1",
                "side_effect_class": "reversible_write",
                "input_schema": {
                    "type": "object",
                    "additionalProperties": False,
                },
                "requires_verification": True,
            },
            self.adapter,
        )

    def test_security_policy_can_authorize_scope_but_still_requires_approval(self):
        result = execute_tool_request(task(), request(), self.registry, auth())
        self.assertEqual("approval_required", result["status"])
        self.assertIn(
            "authorization_binding",
            result["approval"]["extensions"]["security"],
        )
        self.assertEqual(0, self.adapter.calls)

    def test_approval_is_bound_to_current_policy_revision(self):
        first = execute_tool_request(task(), request(), self.registry, auth("r1"))
        approved = dict(first["approval"], status="approved")
        current = auth("r2")
        current["approval"] = approved
        result = execute_tool_request(task(), request(), self.registry, current)
        self.assertEqual("approval_required", result["status"])
        self.assertEqual("stale", result["approval"]["status"])
        self.assertEqual(0, self.adapter.calls)

    def test_approval_is_bound_to_principal(self):
        first = execute_tool_request(task(), request(), self.registry, auth())
        approved = dict(first["approval"], status="approved")
        current = auth(principal=identity(subject="worker-2"))
        current["approval"] = approved
        result = execute_tool_request(task(), request(), self.registry, current)
        self.assertEqual("approval_required", result["status"])
        self.assertEqual("stale", result["approval"]["status"])

    def test_approval_cannot_override_denied_capability(self):
        first = execute_tool_request(task(), request(), self.registry, auth())
        approved = dict(first["approval"], status="approved")
        current = auth(principal=identity(caps=("tools.read",)))
        current["approval"] = approved
        result = execute_tool_request(task(), request(), self.registry, current)
        self.assertEqual("blocked", result["status"])
        self.assertEqual("security_authorization_denied", result["decision_reason"])
        self.assertEqual(0, self.adapter.calls)

    def test_approved_current_authorization_executes(self):
        principal = identity()
        first = execute_tool_request(
            task(), request(), self.registry, auth(principal=principal)
        )
        approved = dict(first["approval"], status="approved")
        current = auth(principal=principal)
        current["approval"] = approved
        result = execute_tool_request(task(), request(), self.registry, current)
        self.assertEqual("executed", result["status"])
        self.assertEqual(1, self.adapter.calls)

    def test_expired_identity_fails_closed_before_adapter(self):
        current = auth(principal=identity(exp=time.time() - 120))
        result = execute_tool_request(task(), request(), self.registry, current)
        self.assertEqual("blocked", result["status"])
        self.assertEqual("invalid_security_context", result["decision_reason"])
        self.assertEqual(0, self.adapter.calls)

    def test_principal_revoked_between_approval_check_and_execution_is_blocked(self):
        principal = identity()
        first = execute_tool_request(
            task(), request(), self.registry, auth(principal=principal)
        )
        approved = dict(first["approval"], status="approved")
        checks = iter((True, False))

        def still_current(_identity: dict) -> bool:
            return next(checks)

        current = auth(
            principal=principal,
            principal_revalidator=still_current,
        )
        current["approval"] = approved
        result = execute_tool_request(task(), request(), self.registry, current)

        self.assertEqual("blocked", result["status"])
        self.assertEqual("security_authorization_stale", result["decision_reason"])
        self.assertEqual(0, self.adapter.calls)


if __name__ == "__main__":
    unittest.main()
