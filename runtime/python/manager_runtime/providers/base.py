from __future__ import annotations

import re
from typing import Any, Protocol, runtime_checkable

ModelPayload = dict[str, Any]

_TOOL_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
_MODEL_RESPONSE_KEYS = {
    "response_id",
    "provider",
    "model",
    "status",
    "output_text",
    "tool_proposals",
    "usage",
    "extensions",
}
_TOOL_PROPOSAL_KEYS = {
    "proposal_id",
    "tool_name",
    "arguments",
    "target",
    "source_ref",
}
_USAGE_KEYS = {"input_tokens", "output_tokens", "total_tokens"}


class ProviderAdapterError(RuntimeError):
    """Raised when a model-provider adapter cannot complete a request."""


@runtime_checkable
class ModelAdapter(Protocol):
    """Provider-neutral interface for model generation and tool proposals."""

    provider: str

    def generate(self, request: ModelPayload) -> ModelPayload:
        """Generate a normalized model response for a normalized request."""


def _validate_tool_definitions(tools: Any) -> None:
    if not isinstance(tools, list):
        raise TypeError("model request tools must be a list")
    for tool in tools:
        if not isinstance(tool, dict):
            raise TypeError("model request tool definitions must be objects")
        for key in ("name", "description", "input_schema"):
            if key not in tool:
                raise ValueError(f"model request tool definition missing {key}")
        if not isinstance(tool["input_schema"], dict):
            raise TypeError("model request tool input_schema must be an object")


def _validate_continuation(continuation: Any) -> None:
    if not isinstance(continuation, dict):
        raise TypeError("model request continuation must be an object")
    prior = continuation.get("prior_response_ref")
    if not isinstance(prior, str) or not prior:
        raise ValueError("model request continuation requires prior_response_ref")
    results = continuation.get("tool_results")
    if not isinstance(results, list) or not results:
        raise ValueError("model request continuation requires tool_results")
    seen: set[str] = set()
    for result in results:
        if not isinstance(result, dict):
            raise TypeError("continuation tool results must be objects")
        proposal_id = result.get("proposal_id")
        if not isinstance(proposal_id, str) or not proposal_id:
            raise ValueError("continuation tool result requires proposal_id")
        if proposal_id in seen:
            raise ValueError("continuation tool result proposal_id values must be unique")
        seen.add(proposal_id)
        if result.get("status") != "executed":
            raise ValueError("only executed tool results may continue a model turn")
        if not isinstance(result.get("output"), str):
            raise TypeError("continuation tool result output must be text")
        if "redacted" in result and not isinstance(result["redacted"], bool):
            raise TypeError("continuation tool result redacted must be boolean")


def validate_model_request(request: ModelPayload) -> None:
    required = ("request_id", "model", "input")
    missing = [key for key in required if not request.get(key)]
    if missing:
        raise ValueError(f"model request missing required fields: {', '.join(missing)}")
    if not isinstance(request["input"], str):
        raise TypeError("model request input must be text in the reference adapter")
    if "instructions" in request and not isinstance(request["instructions"], str):
        raise TypeError("model request instructions must be text")
    if "max_output_tokens" in request:
        value = request["max_output_tokens"]
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise TypeError("max_output_tokens must be a positive integer")
    if "tools" in request:
        _validate_tool_definitions(request["tools"])
    if "continuation" in request:
        _validate_continuation(request["continuation"])


def _validate_usage(value: Any) -> None:
    if not isinstance(value, dict):
        raise TypeError("model response usage must be an object")
    missing = sorted(_USAGE_KEYS - set(value))
    if missing:
        raise ValueError(f"model response usage missing required fields: {', '.join(missing)}")
    unknown = sorted(set(value) - _USAGE_KEYS)
    if unknown:
        raise ValueError(f"model response usage has unknown fields: {', '.join(unknown)}")
    for key in sorted(_USAGE_KEYS):
        count = value[key]
        if count is None:
            continue
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise TypeError(f"model response usage {key} must be a non-negative integer or null")


def _validate_tool_proposals(proposals: Any, *, response_status: str) -> None:
    if not isinstance(proposals, list):
        raise TypeError("model response tool_proposals must be a list")
    if response_status != "completed" and proposals:
        raise ValueError("non-completed model responses must not contain tool proposals")

    seen: set[str] = set()
    for proposal in proposals:
        if not isinstance(proposal, dict):
            raise TypeError("tool proposal must be an object")
        unknown = sorted(set(proposal) - _TOOL_PROPOSAL_KEYS)
        if unknown:
            raise ValueError(f"tool proposal has unknown fields: {', '.join(unknown)}")
        for key in ("proposal_id", "tool_name", "arguments"):
            if key not in proposal:
                raise ValueError(f"tool proposal missing required field: {key}")

        proposal_id = proposal["proposal_id"]
        if not isinstance(proposal_id, str) or not proposal_id:
            raise ValueError("tool proposal proposal_id must be non-empty text")
        if proposal_id in seen:
            raise ValueError("tool proposal proposal_id values must be unique")
        seen.add(proposal_id)

        tool_name = proposal["tool_name"]
        if not isinstance(tool_name, str) or not _TOOL_NAME.fullmatch(tool_name):
            raise ValueError("tool proposal tool_name is invalid")
        if not isinstance(proposal["arguments"], dict):
            raise TypeError("tool proposal arguments must be an object")
        for key in ("target", "source_ref"):
            if key in proposal and proposal[key] is not None and not isinstance(proposal[key], str):
                raise TypeError(f"tool proposal {key} must be text or null")


def validate_model_response(
    response: ModelPayload,
    *,
    expected_provider: str | None = None,
) -> None:
    """Validate the normalized model-response contract and runtime invariants.

    The checks intentionally mirror contracts/model-response.schema.json rather
    than trusting adapters to have normalized provider output correctly.
    """
    if not isinstance(response, dict):
        raise TypeError("model response must be an object")

    required = ("response_id", "provider", "model", "status", "output_text", "usage")
    missing = [key for key in required if key not in response]
    if missing:
        raise ValueError(f"model response missing required fields: {', '.join(missing)}")
    unknown = sorted(set(response) - _MODEL_RESPONSE_KEYS)
    if unknown:
        raise ValueError(f"model response has unknown fields: {', '.join(unknown)}")

    response_id = response["response_id"]
    if response_id is not None and not isinstance(response_id, str):
        raise TypeError("model response response_id must be text or null")

    provider = response["provider"]
    if not isinstance(provider, str) or not provider:
        raise ValueError("model response provider must be non-empty text")
    if expected_provider is not None and provider != expected_provider:
        raise ValueError("model response provider does not match the invoked adapter")

    model = response["model"]
    if not isinstance(model, str) or not model:
        raise ValueError("model response model must be non-empty text")

    status = response["status"]
    if status not in {"completed", "incomplete", "failed"}:
        raise ValueError("model response status is not normalized")
    if not isinstance(response["output_text"], str):
        raise TypeError("model response output_text must be text")

    _validate_tool_proposals(response.get("tool_proposals", []), response_status=status)
    _validate_usage(response["usage"])

    if "extensions" in response and not isinstance(response["extensions"], dict):
        raise TypeError("model response extensions must be an object")
