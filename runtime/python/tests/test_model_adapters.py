from __future__ import annotations

import unittest
from types import SimpleNamespace

from manager_runtime.orchestrator import run_with_model
from manager_runtime.providers.openai_adapter import OpenAIResponsesAdapter


def payload(*, materiality: str = "routine", sensitivity: str = "public") -> dict:
    return {
        "task": {
            "task_id": "model-test",
            "objective": "Write one short public sentence.",
            "classification": {
                "materiality": materiality,
                "consequence": "low" if materiality == "routine" else "high",
                "uncertainty": "low",
                "reversibility": "reversible",
                "sensitivity": sensitivity,
            },
        },
        "trusted_policy_context": [],
        "untrusted_content": [],
        "model_input": "Write one short public sentence.",
    }


class FakeResponses:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            id="resp_test",
            model=kwargs["model"],
            status="completed",
            output_text="A bounded model response.",
            usage=SimpleNamespace(input_tokens=5, output_tokens=4, total_tokens=9),
        )


class FakeClient:
    def __init__(self) -> None:
        self.responses = FakeResponses()


class RecordingAdapter:
    provider = "recording"

    def __init__(self) -> None:
        self.calls = []

    def generate(self, request):
        self.calls.append(request)
        return {
            "response_id": "recording:1",
            "provider": self.provider,
            "model": request["model"],
            "status": "completed",
            "output_text": "Recorded response.",
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        }


class ModelAdapterTests(unittest.TestCase):
    def test_openai_responses_mapping_uses_normalized_contract(self) -> None:
        client = FakeClient()
        adapter = OpenAIResponsesAdapter(client=client)
        response = adapter.generate(
            {
                "request_id": "req-1",
                "model": "gpt-test",
                "instructions": "Answer briefly.",
                "input": "Hello",
                "max_output_tokens": 40,
            }
        )
        self.assertEqual(client.responses.calls[0]["input"], "Hello")
        self.assertEqual(client.responses.calls[0]["max_output_tokens"], 40)
        self.assertEqual(response["provider"], "openai")
        self.assertEqual(response["output_text"], "A bounded model response.")
        self.assertEqual(response["usage"]["total_tokens"], 9)

    def test_direct_public_task_can_use_model_adapter(self) -> None:
        adapter = RecordingAdapter()
        output = run_with_model(payload(), adapter, model="example-model")
        self.assertEqual(len(adapter.calls), 1)
        self.assertEqual(output["trace"]["workflow"], "direct")
        self.assertEqual(output["trace"]["events"][-1]["event_type"], "model")
        self.assertEqual(output["result"]["finding"], "Recorded response.")
        self.assertEqual(output["model_response"]["provider"], "recording")

    def test_material_task_never_reaches_model_provider(self) -> None:
        adapter = RecordingAdapter()
        output = run_with_model(
            payload(materiality="material"), adapter, model="example-model"
        )
        self.assertEqual(adapter.calls, [])
        self.assertEqual(output["trace"]["status"], "blocked")
        self.assertEqual(output["model"]["reason"], "control_plane_blocked")

    def test_non_public_input_is_not_sent_without_explicit_opt_in(self) -> None:
        adapter = RecordingAdapter()
        output = run_with_model(
            payload(sensitivity="sensitive"), adapter, model="example-model"
        )
        self.assertEqual(adapter.calls, [])
        self.assertEqual(
            output["model"]["reason"], "non_public_input_requires_explicit_opt_in"
        )


if __name__ == "__main__":
    unittest.main()
