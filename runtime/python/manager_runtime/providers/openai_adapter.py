from __future__ import annotations

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


class OpenAIResponsesAdapter:
    """Reference OpenAI adapter using the Responses API.

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
            "usage": normalized_usage,
        }
        validate_model_response(response)
        return response
