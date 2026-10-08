from __future__ import annotations

import unittest
from types import SimpleNamespace

from manager_runtime.orchestrator import run_with_model
from manager_runtime.providers.base import validate_model_response
from manager_runtime.providers.openai_adapter import OpenAIResponsesAdapter
from manager_runtime.tools import ToolRegistry, execute_tool_request


def payload() -> dict:
    return {
        "task": {
            "task_id": "p1-runtime",
            "objective": "Return a bounded result.",
            "classification": {
                "materiality": "routine",
                "consequence": "low",
                "uncertainty": "low",
                "reversibility": "reversible",
                "sensitivity": "public",
            },
        },
        "trusted_policy_context": [],
        "untrusted_content": [],
        "model_input": "Return a bounded result.",
    }


def response(*, status: str = "completed", text: str = "done") -> dict:
    return {
        "response_id": "response:p1",
        "provider": "synthetic",
        "model": "synthetic-model",
        "status": status,
        "output_text": text,
        "tool_proposals": [],
        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
    }


class StaticAdapter:
    provider = "synthetic"

    def __init__(self, value: dict) -> None:
        self.value = value

    def generate(self, request: dict) -> dict:
        return dict(self.value)


class RaisingAdapter:
    provider = "synthetic"

    def generate(self, request: dict) -> dict:
        raise RuntimeError("secret-provider-token=do-not-leak")


class RaisingTool:
    def execute(self, arguments: dict):
        raise RuntimeError("secret-tool-token=do-not-leak")


class MissingStatusResponses:
    def create(self, **kwargs):
        return SimpleNamespace(
            id="response:missing-status",
            model=kwargs["model"],
            output_text="provider did not declare completion",
            usage=SimpleNamespace(input_tokens=1, output_tokens=2, total_tokens=3),
        )


class MissingStatusClient:
    def __init__(self) -> None:
        self.responses = MissingStatusResponses()


class P1RuntimeHardeningTests(unittest.TestCase):
    def test_failed_model_response_cannot_leave_successful_result(self) -> None:
        output = run_with_model(
            payload(),
            StaticAdapter(response(status="failed", text="")),
            model="synthetic-model",
        )
        self.assertEqual(output["trace"]["status"], "failed")
        self.assertEqual(output["result"]["status"], "failed")
        self.assertIn("failed", output["result"]["finding"].lower())

    def test_incomplete_model_response_becomes_partial_and_blocked(self) -> None:
        output = run_with_model(
            payload(),
            StaticAdapter(response(status="incomplete", text="partial text")),
            model="synthetic-model",
        )
        self.assertEqual(output["trace"]["status"], "blocked")
        self.assertEqual(output["result"]["status"], "partial")
        self.assertEqual(output["result"]["finding"], "partial text")

    def test_provider_exception_message_is_not_exposed(self) -> None:
        output = run_with_model(payload(), RaisingAdapter(), model="synthetic-model")
        rendered = repr(output)
        self.assertEqual(output["trace"]["status"], "failed")
        self.assertNotIn("secret-provider-token", rendered)
        self.assertIn("RuntimeError", rendered)

    def test_provider_identity_mismatch_fails_governed_boundary(self) -> None:
        malformed = response()
        malformed["provider"] = "impersonated-provider"
        output = run_with_model(
            payload(),
            StaticAdapter(malformed),
            model="synthetic-model",
        )
        self.assertEqual(output["trace"]["status"], "failed")
        self.assertEqual(output["result"]["status"], "failed")
        self.assertNotIn("model_response", output)

    def test_response_contract_rejects_duplicate_proposal_ids(self) -> None:
        malformed = response()
        malformed["tool_proposals"] = [
            {
                "proposal_id": "dup",
                "tool_name": "lookup",
                "arguments": {},
            },
            {
                "proposal_id": "dup",
                "tool_name": "lookup",
                "arguments": {},
            },
        ]
        with self.assertRaisesRegex(ValueError, "must be unique"):
            validate_model_response(malformed)

    def test_response_contract_rejects_unknown_fields_and_bad_usage(self) -> None:
        malformed = response()
        malformed["debug_secret"] = "should-not-cross-boundary"
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            validate_model_response(malformed)

        malformed = response()
        malformed["usage"]["total_tokens"] = -1
        with self.assertRaisesRegex(TypeError, "non-negative"):
            validate_model_response(malformed)

    def test_noncompleted_response_cannot_smuggle_tool_proposal(self) -> None:
        malformed = response(status="failed")
        malformed["tool_proposals"] = [
            {
                "proposal_id": "call-1",
                "tool_name": "lookup",
                "arguments": {},
            }
        ]
        with self.assertRaisesRegex(ValueError, "must not contain tool proposals"):
            validate_model_response(malformed)

    def test_missing_openai_status_is_incomplete_not_completed(self) -> None:
        adapter = OpenAIResponsesAdapter(client=MissingStatusClient())
        normalized = adapter.generate(
            {
                "request_id": "request:p1",
                "model": "gpt-test",
                "input": "hello",
            }
        )
        self.assertEqual(normalized["status"], "incomplete")

    def test_tool_exception_message_is_not_exposed(self) -> None:
        registry = ToolRegistry()
        registry.register(
            {
                "name": "explode",
                "description": "Synthetic failing read.",
                "side_effect_class": "read",
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                "requires_verification": False,
                "sensitive_output": False,
            },
            RaisingTool(),
        )
        result = execute_tool_request(
            payload()["task"],
            {
                "request_id": "tool-request:p1",
                "run_id": "run:p1",
                "tool_name": "explode",
                "arguments": {},
                "target": None,
                "proposed_by": "model",
            },
            registry,
            {"scope_authorized": True},
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"], "RuntimeError")
        self.assertNotIn("secret-tool-token", repr(result))


if __name__ == "__main__":
    unittest.main()
