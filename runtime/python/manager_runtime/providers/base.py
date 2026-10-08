from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

ModelPayload = dict[str, Any]


class ProviderAdapterError(RuntimeError):
    """Raised when a model-provider adapter cannot complete a request."""


@runtime_checkable
class ModelAdapter(Protocol):
    """Provider-neutral interface for text model generation."""

    provider: str

    def generate(self, request: ModelPayload) -> ModelPayload:
        """Generate a normalized model response for a normalized request."""


def validate_model_request(request: ModelPayload) -> None:
    required = ("request_id", "model", "input")
    missing = [key for key in required if not request.get(key)]
    if missing:
        raise ValueError(f"model request missing required fields: {', '.join(missing)}")
    if not isinstance(request["input"], str):
        raise TypeError("model request input must be text in the v1 reference adapter")
    if "instructions" in request and not isinstance(request["instructions"], str):
        raise TypeError("model request instructions must be text")
    if "max_output_tokens" in request:
        value = request["max_output_tokens"]
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise TypeError("max_output_tokens must be a positive integer")


def validate_model_response(response: ModelPayload) -> None:
    required = ("provider", "model", "status", "output_text")
    missing = [key for key in required if key not in response]
    if missing:
        raise ValueError(f"model response missing required fields: {', '.join(missing)}")
    if response["status"] not in {"completed", "incomplete", "failed"}:
        raise ValueError("model response status is not normalized")
    if not isinstance(response["output_text"], str):
        raise TypeError("model response output_text must be text")
