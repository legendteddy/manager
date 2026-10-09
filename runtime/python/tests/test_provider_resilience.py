from __future__ import annotations

import unittest
from types import SimpleNamespace

from manager_runtime.providers.base import (
    ProviderAuthenticationError,
    ProviderAuthorizationError,
    ProviderCapabilities,
    ProviderContextLimitError,
    ProviderMalformedResponseError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    validate_model_request,
)
from manager_runtime.providers.openai_adapter import OpenAIResponsesAdapter
from manager_runtime.providers.resilience import ProviderRetryPolicy, ProviderRoute, ResilientModelAdapter
from manager_runtime.providers.synthetic import SyntheticModelAdapter


def request(**overrides):
    value = {"request_id": "request:1", "model": "synthetic-model", "input": "bounded"}
    value.update(overrides)
    return value


def tool_definition():
    return {
        "name": "lookup",
        "description": "Synthetic lookup.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    }


def proposal(response_id="synthetic:tool:1"):
    return {
        "proposal_id": "call:1",
        "tool_name": "lookup",
        "arguments": {},
        "source_ref": response_id,
    }


def continuation(prior="synthetic:tool:1"):
    return {
        "prior_response_ref": prior,
        "tool_results": [{"proposal_id": "call:1", "status": "executed", "output": "{}"}],
    }


class FakeResponses:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result


class FakeClient:
    def __init__(self, result=None, error=None):
        self.responses = FakeResponses(result=result, error=error)


class FakeHTTPError(Exception):
    def __init__(self, status_code, *, code=None):
        super().__init__("secret remote provider detail")
        self.status_code = status_code
        self.code = code


class ProviderResilienceTests(unittest.TestCase):
    @staticmethod
    def policy(attempts=2):
        return ProviderRetryPolicy(
            max_attempts=attempts,
            base_backoff_seconds=0,
            max_backoff_seconds=0,
            timeout_seconds=3.5,
        )

    def resilient(self, routes, *, attempts=2, pinned_provider=None):
        return ResilientModelAdapter(
            routes,
            retry_policy=self.policy(attempts),
            pinned_provider=pinned_provider,
            sleep=lambda _: None,
        )

    def test_request_contract_rejects_unknown_fields_and_duplicate_tools(self):
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            validate_model_request(request(debug="not-canonical"))
        with self.assertRaisesRegex(ValueError, "must be unique"):
            validate_model_request(request(tools=[tool_definition(), tool_definition()]))

    def test_synthetic_provider_covers_tool_and_continuation_contract(self):
        adapter = SyntheticModelAdapter(
            [
                SyntheticModelAdapter.response(
                    response_id="synthetic:tool:1", tool_proposals=[proposal()]
                ),
                SyntheticModelAdapter.response(response_id="synthetic:final:2", text="done"),
            ]
        )
        first = adapter.generate(request(tools=[tool_definition()]))
        self.assertEqual(first["tool_proposals"][0]["proposal_id"], "call:1")
        final = adapter.generate(request(request_id="request:2", continuation=continuation()))
        self.assertEqual(final["output_text"], "done")

    def test_rate_limit_retry_is_bounded_and_visible(self):
        adapter = SyntheticModelAdapter(
            [ProviderRateLimitError("synthetic"), SyntheticModelAdapter.response(text="ok")]
        )
        result = self.resilient([adapter]).generate(request())
        attempts = result["extensions"]["manager_runtime"]["attempts"]
        self.assertEqual([item["outcome"] for item in attempts], ["rate_limit", "success"])
        self.assertEqual(len(adapter.calls), 2)

    def test_authentication_failure_neither_retries_nor_fails_over(self):
        primary = SyntheticModelAdapter([ProviderAuthenticationError("primary")], provider="primary")
        fallback = SyntheticModelAdapter(
            [SyntheticModelAdapter.response(provider="fallback")], provider="fallback"
        )
        result = self.resilient([primary, fallback]).generate(request())
        self.assertEqual(result["status"], "failed")
        self.assertEqual(
            result["extensions"]["manager_runtime"]["provider_error"]["category"],
            "authentication_failure",
        )
        self.assertEqual(len(primary.calls), 1)
        self.assertEqual(fallback.calls, [])

    def test_initial_transient_failure_can_fail_over_and_pin_route(self):
        primary = SyntheticModelAdapter(
            [ProviderUnavailableError("primary"), ProviderUnavailableError("primary")],
            provider="primary",
        )
        fallback = SyntheticModelAdapter(
            [SyntheticModelAdapter.response(provider="fallback", model="fallback-model", text="ok")],
            provider="fallback",
        )
        adapter = self.resilient(
            [ProviderRoute(primary), ProviderRoute(fallback, model="fallback-model")]
        )
        result = adapter.generate(request())
        meta = result["extensions"]["manager_runtime"]["resilience"]
        self.assertEqual(result["provider"], "fallback")
        self.assertTrue(meta["failover_used"])
        self.assertEqual(adapter.selected_provider, "fallback")

    def test_capability_and_context_differences_can_select_initial_fallback(self):
        incapable = SyntheticModelAdapter(
            [],
            provider="incapable",
            capabilities=ProviderCapabilities(tools=False, request_timeout=True),
        )
        capable = SyntheticModelAdapter(
            [SyntheticModelAdapter.response(provider="capable")], provider="capable"
        )
        self.assertEqual(
            self.resilient([incapable, capable]).generate(request(tools=[tool_definition()]))["provider"],
            "capable",
        )

        small = SyntheticModelAdapter([ProviderContextLimitError("small")], provider="small")
        large = SyntheticModelAdapter(
            [SyntheticModelAdapter.response(provider="large")], provider="large"
        )
        self.assertEqual(
            self.resilient([small, large], attempts=1).generate(request())["provider"],
            "large",
        )

    def test_malformed_response_does_not_hide_behind_failover(self):
        primary = SyntheticModelAdapter(
            [ProviderMalformedResponseError("primary")], provider="primary"
        )
        fallback = SyntheticModelAdapter(
            [SyntheticModelAdapter.response(provider="fallback")], provider="fallback"
        )
        result = self.resilient([primary, fallback]).generate(request())
        self.assertEqual(result["status"], "failed")
        self.assertEqual(fallback.calls, [])

    def test_continuation_never_switches_provider_after_response_identity(self):
        primary = SyntheticModelAdapter(
            [
                SyntheticModelAdapter.response(
                    provider="primary", response_id="primary:1", tool_proposals=[proposal("primary:1")]
                ),
                ProviderTimeoutError("primary"),
            ],
            provider="primary",
        )
        fallback = SyntheticModelAdapter(
            [SyntheticModelAdapter.response(provider="fallback", text="unsafe")],
            provider="fallback",
        )
        adapter = self.resilient([primary, fallback], attempts=1)
        adapter.generate(request(tools=[tool_definition()]))
        result = adapter.generate(request(request_id="request:2", continuation=continuation("primary:1")))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["provider"], "primary")
        self.assertEqual(fallback.calls, [])

    def test_fresh_continuation_requires_explicit_provider_pin(self):
        provider = SyntheticModelAdapter([SyntheticModelAdapter.response(text="must not run")])
        result = self.resilient([provider]).generate(request(continuation=continuation("unknown:1")))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(provider.calls, [])

        primary = SyntheticModelAdapter([], provider="primary")
        secondary = SyntheticModelAdapter(
            [SyntheticModelAdapter.response(provider="secondary", text="resumed")],
            provider="secondary",
        )
        result = self.resilient(
            [primary, secondary], pinned_provider="secondary"
        ).generate(request(continuation=continuation("secondary:prior")))
        self.assertEqual(result["provider"], "secondary")
        self.assertEqual(primary.calls, [])

    def test_timeouts_are_explicit_and_bound_initial_tool_and_final_turns(self):
        for payload in (request(), request(tools=[tool_definition()])):
            provider = SyntheticModelAdapter([ProviderTimeoutError("synthetic")])
            result = self.resilient([provider], attempts=1).generate(payload)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(
                result["extensions"]["manager_runtime"]["provider_error"]["category"],
                "timeout",
            )

        first = SyntheticModelAdapter.response(
            response_id="synthetic:tool:1", tool_proposals=[proposal()]
        )
        provider = SyntheticModelAdapter([first, ProviderTimeoutError("synthetic")])
        adapter = self.resilient([provider], attempts=1)
        adapter.generate(request(tools=[tool_definition()]))
        final = adapter.generate(request(request_id="request:final", continuation=continuation()))
        self.assertEqual(final["status"], "failed")

    def test_openai_timeout_is_forwarded_to_sdk_call(self):
        raw = SimpleNamespace(
            id="resp:1",
            model="gpt-test",
            status="completed",
            output_text="ok",
            output=[],
            usage=SimpleNamespace(input_tokens=1, output_tokens=1, total_tokens=2),
        )
        client = FakeClient(result=raw)
        OpenAIResponsesAdapter(client=client).generate(
            request(
                model="gpt-test",
                extensions={"manager_runtime": {"timeout_seconds": 4.25}},
            )
        )
        self.assertEqual(client.responses.calls[0]["timeout"], 4.25)

    def test_openai_sdk_failures_are_normalized_without_raw_message(self):
        cases = [
            (FakeHTTPError(401), ProviderAuthenticationError),
            (FakeHTTPError(403), ProviderAuthorizationError),
            (FakeHTTPError(429), ProviderRateLimitError),
            (FakeHTTPError(503), ProviderUnavailableError),
            (TimeoutError("secret timeout"), ProviderTimeoutError),
            (FakeHTTPError(400, code="context_length_exceeded"), ProviderContextLimitError),
        ]
        for error, expected in cases:
            with self.subTest(expected=expected.__name__):
                with self.assertRaises(expected) as caught:
                    OpenAIResponsesAdapter(client=FakeClient(error=error)).generate(
                        request(model="gpt-test")
                    )
                self.assertNotIn("secret", str(caught.exception))

    def test_openai_missing_response_or_tool_identity_fails_closed(self):
        missing_response = SimpleNamespace(
            id=None,
            model="gpt-test",
            status="completed",
            output_text="",
            output=[],
            usage=SimpleNamespace(input_tokens=1, output_tokens=1, total_tokens=2),
        )
        with self.assertRaises(ProviderMalformedResponseError):
            OpenAIResponsesAdapter(client=FakeClient(result=missing_response)).generate(
                request(model="gpt-test")
            )

        missing_call = SimpleNamespace(
            id="resp:1",
            model="gpt-test",
            status="completed",
            output_text="",
            output=[SimpleNamespace(type="function_call", name="lookup", arguments="{}")],
            usage=SimpleNamespace(input_tokens=1, output_tokens=1, total_tokens=2),
        )
        with self.assertRaises(ProviderMalformedResponseError):
            OpenAIResponsesAdapter(client=FakeClient(result=missing_call)).generate(
                request(model="gpt-test", tools=[tool_definition()])
            )

    def test_openai_malformed_arguments_status_reason_and_output_type_fail_closed(self):
        usage = SimpleNamespace(input_tokens=1, output_tokens=1, total_tokens=2)
        malformed_args = SimpleNamespace(
            id="resp:1",
            model="gpt-test",
            status="completed",
            output_text="",
            output=[SimpleNamespace(type="function_call", call_id="call:1", name="lookup", arguments="{")],
            usage=usage,
        )
        with self.assertRaises(ProviderMalformedResponseError):
            OpenAIResponsesAdapter(client=FakeClient(result=malformed_args)).generate(
                request(model="gpt-test", tools=[tool_definition()])
            )

        for raw in (
            SimpleNamespace(id="resp:1", model="gpt-test", status="surprising", output_text="", output=[], usage=usage),
            SimpleNamespace(
                id="resp:1",
                model="gpt-test",
                status="incomplete",
                incomplete_details=SimpleNamespace(reason="unknown_stop_reason"),
                output_text="",
                output=[],
                usage=usage,
            ),
            SimpleNamespace(id="resp:1", model="gpt-test", status="completed", output_text=["wrong"], output=[], usage=usage),
        ):
            with self.assertRaises(ProviderMalformedResponseError):
                OpenAIResponsesAdapter(client=FakeClient(result=raw)).generate(request(model="gpt-test"))

    def test_failed_artifact_excludes_provider_exception_text(self):
        provider = SyntheticModelAdapter([RuntimeError("secret-provider-token=do-not-leak")])
        result = self.resilient([provider], attempts=1).generate(request())
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("secret-provider-token", repr(result))


if __name__ == "__main__":
    unittest.main()
