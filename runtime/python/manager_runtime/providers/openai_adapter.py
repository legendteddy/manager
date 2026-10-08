from __future__ import annotations

import json
from typing import Any

from .base import (
    ModelPayload,
    ProviderAdapterError,
    validate_model_request,
    validate_model_response,
)


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _normalize_status(value: Any) -> str:
    status = str(value or "completed").lower()
    if status in {"completed", "complete"}:
        return "completed"
    if status in {"failed", "error"}:
        return "failed"
    return "incomplete"


def _openai_tools(request: ModelPayload) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    for definition in request.get("tools", []):
        tools.append(
            {
                "type": "function",
                "name": definition["name"],
                "description": definition["description"],
                "parameters": definition["input_schema"],
                "strict": True,
            }
        )
    return tools


def _tool_proposals(raw: Any) -> list[ModelPayload]:
    proposals: list[ModelPayload] = []
    response_id = _field(raw, "id")
    for index, item in enumerate(_field(raw, "output", []) or []):
        if _field(item, "type") != "function_call":
            continue
        raw_arguments = _field(item, "arguments", "{}")
        try:
            arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
        except json.JSONDecodeError as exc:
            raise ProviderAdapterError("OpenAI returned malformed function-call arguments") from exc
        if not isinstance(arguments, dict):
            raise ProviderAdapterError("OpenAI function-call arguments must decode to an object")
        proposal_id = _field(item, "call_id") or _field(item, "id") or f"proposal:{index}"
        target = arguments.get("target") if isinstance(arguments.get("target"), str) else None
        proposals.append(
            {
                "proposal_id": str(proposal_id),
                "tool_name": str(_field(item, "name", "")),
                "arguments": arguments,
                "target": target,
                "source_ref": str(response_id) if response_id is not None else None,
            }
        )
    return proposals


class OpenAIResponsesAdapter:
    """Reference OpenAI adapter using the Responses API.

    Stage 5 exposes only custom function definitions. The provider may propose
    function calls, but this adapter never executes them. Manager's governed
    tool runtime owns execution and approval decisions.

    The OpenAI SDK is optional and imported only when a client is not injected.
    Tests can inject a compatible fake client without credentials or network use.
    """

    provider = "openai"

    def __init__(self, client: Any | None = None) -> None:
        if client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise ProviderAdapterError(
                    "OpenAI adapter requires the optional 'openai' dependency"
                ) from exc
            client = OpenAI()
        self._client = client

    def generate(self, request: ModelPayload) -> ModelPayload:
        validate_model_request(request)
        kwargs: dict[str, Any] = {
            "model": request["model"],
            "input": request["input"],
        }
        instructions = request.get("instructions")
        if instructions:
            kwargs["instructions"] = instructions
        if request.get("max_output_tokens") is not None:
            kwargs["max_output_tokens"] = request["max_output_tokens"]
        if request.get("tools"):
            kwargs["tools"] = _openai_tools(request)

        try:
            raw = self._client.responses.create(**kwargs)
        except Exception as exc:
            raise ProviderAdapterError(
                f"OpenAI Responses request failed: {type(exc).__name__}"
            ) from exc

        usage = _field(raw, "usage")
        normalized_usage = {
            "input_tokens": _field(usage, "input_tokens"),
            "output_tokens": _field(usage, "output_tokens"),
            "total_tokens": _field(usage, "total_tokens"),
        }
        response: ModelPayload = {
            "response_id": _field(raw, "id"),
            "provider": self.provider,
            "model": str(_field(raw, "model", request["model"])),
            "status": _normalize_status(_field(raw, "status", "completed")),
            "output_text": str(_field(raw, "output_text", "") or ""),
            "tool_proposals": _tool_proposals(raw),
            "usage": normalized_usage,
        }
        validate_model_response(response)
        return response
