from __future__ import annotations

from copy import deepcopy
from typing import Callable, Iterable

from .base import (
    ModelPayload,
    ProviderCapabilities,
    ProviderInternalError,
    ProviderMalformedResponseError,
    normalize_provider_exception,
    validate_model_request,
    validate_model_response,
)

SyntheticStep = ModelPayload | Exception | Callable[[ModelPayload], ModelPayload]


class SyntheticModelAdapter:
    """Deterministic provider used to exercise Manager's provider boundary.

    The adapter has no network, credentials, billing, or external side effects.
    A script may contain normalized responses, sanitized provider errors, or
    callables that derive one normalized response from the request.
    """

    def __init__(
        self,
        script: Iterable[SyntheticStep],
        *,
        provider: str = "synthetic",
        capabilities: ProviderCapabilities | None = None,
    ) -> None:
        if not isinstance(provider, str) or not provider:
            raise ValueError("provider must be non-empty text")
        self.provider = provider
        self.capabilities = capabilities or ProviderCapabilities(
            tools=True,
            structured_output=True,
            continuation=True,
            streaming=False,
            request_timeout=True,
            context_window_tokens=131072,
            continuation_family="manager.synthetic/v1",
        )
        self._script = list(script)
        self.calls: list[ModelPayload] = []

    def get_capabilities(self, model: str) -> ProviderCapabilities:
        return self.capabilities

    @staticmethod
    def response(
        *,
        provider: str = "synthetic",
        response_id: str = "synthetic:response:1",
        model: str = "synthetic-model",
        text: str = "",
        status: str = "completed",
        tool_proposals: list[ModelPayload] | None = None,
        usage: ModelPayload | None = None,
        extensions: ModelPayload | None = None,
    ) -> ModelPayload:
        value: ModelPayload = {
            "response_id": response_id,
            "provider": provider,
            "model": model,
            "status": status,
            "output_text": text,
            "tool_proposals": deepcopy(tool_proposals or []),
            "usage": deepcopy(
                usage
                or {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}
            ),
        }
        if extensions is not None:
            value["extensions"] = deepcopy(extensions)
        return value

    def generate(self, request: ModelPayload) -> ModelPayload:
        validate_model_request(request)
        self.calls.append(deepcopy(request))
        if not self._script:
            raise ProviderInternalError(
                self.provider, detail="synthetic provider script is exhausted"
            )
        step = self._script.pop(0)
        if isinstance(step, Exception):
            error = normalize_provider_exception(self.provider, step)
            raise error from step
        try:
            response = step(deepcopy(request)) if callable(step) else deepcopy(step)
        except Exception as exc:
            error = normalize_provider_exception(self.provider, exc)
            raise error from exc
        if not isinstance(response, dict):
            raise ProviderInternalError(
                self.provider, detail="synthetic script did not return a model response"
            )
        try:
            validate_model_response(response, expected_provider=self.provider)
        except (TypeError, ValueError) as exc:
            raise ProviderMalformedResponseError(
                self.provider, detail="synthetic scripted response failed validation"
            ) from exc
        return response
