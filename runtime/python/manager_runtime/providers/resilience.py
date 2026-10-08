from __future__ import annotations

import time
from copy import deepcopy
from dataclasses import dataclass
from typing import Callable, Iterable

from .base import (
    ModelAdapter,
    ModelPayload,
    ProviderAdapterError,
    ProviderCapabilities,
    ProviderMalformedResponseError,
    ProviderUnsupportedCapabilityError,
    adapter_capabilities,
    normalize_provider_exception,
    validate_model_request,
    validate_model_response,
)

_RUNTIME_EXTENSION = "manager_runtime"


@dataclass(frozen=True)
class ProviderRoute:
    """One eligible provider route for a new model step.

    ``model`` may override the canonical request model for non-durable initial
    failover. Durable workflows should use one stable model identity per run.
    """

    adapter: ModelAdapter
    model: str | None = None


@dataclass(frozen=True)
class ProviderRetryPolicy:
    """Bounded provider-call retry and timeout policy."""

    max_attempts: int = 2
    base_backoff_seconds: float = 0.25
    max_backoff_seconds: float = 2.0
    timeout_seconds: float | None = 60.0

    def __post_init__(self) -> None:
        if not isinstance(self.max_attempts, int) or isinstance(self.max_attempts, bool):
            raise TypeError("max_attempts must be an integer")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        for name, value in (
            ("base_backoff_seconds", self.base_backoff_seconds),
            ("max_backoff_seconds", self.max_backoff_seconds),
        ):
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a non-negative number")
        if self.max_backoff_seconds < self.base_backoff_seconds:
            raise ValueError("max_backoff_seconds must be >= base_backoff_seconds")
        if self.timeout_seconds is not None and (
            not isinstance(self.timeout_seconds, (int, float))
            or isinstance(self.timeout_seconds, bool)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be positive or null")

    def backoff_seconds(self, failed_attempt: int) -> float:
        value = self.base_backoff_seconds * (2 ** max(0, failed_attempt - 1))
        return min(float(value), float(self.max_backoff_seconds))


class ResilientModelAdapter:
    """Provider-neutral retry/failover wrapper with fail-closed continuation.

    Failover is allowed only before a provider has been selected and only for a
    request without a continuation envelope. Once a response is accepted, all
    later calls stay pinned to that provider route. This prevents a tool-result
    continuation from being silently replayed into an incompatible provider.

    Provider errors are returned as normalized failed model responses rather
    than leaking SDK exceptions through bounded-loop continuation paths.
    """

    def __init__(
        self,
        routes: Iterable[ProviderRoute | ModelAdapter],
        *,
        retry_policy: ProviderRetryPolicy | None = None,
        pinned_provider: str | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        normalized: list[ProviderRoute] = []
        for route in routes:
            normalized.append(route if isinstance(route, ProviderRoute) else ProviderRoute(route))
        if not normalized:
            raise ValueError("at least one provider route is required")
        providers = [route.adapter.provider for route in normalized]
        if len(set(providers)) != len(providers):
            raise ValueError("provider route identities must be unique")
        self._routes = normalized
        self._policy = retry_policy or ProviderRetryPolicy()
        self._sleep = sleep
        self._selected_index: int | None = None
        if pinned_provider is not None:
            try:
                self._selected_index = providers.index(pinned_provider)
            except ValueError as exc:
                raise ValueError("pinned_provider is not present in provider routes") from exc

    @property
    def provider(self) -> str:
        if self._selected_index is not None:
            return self._routes[self._selected_index].adapter.provider
        return self._routes[0].adapter.provider

    @property
    def selected_provider(self) -> str | None:
        if self._selected_index is None:
            return None
        return self._routes[self._selected_index].adapter.provider

    def get_capabilities(self, model: str) -> ProviderCapabilities:
        if self._selected_index is not None:
            route = self._routes[self._selected_index]
            return adapter_capabilities(route.adapter, route.model or model)
        # Before selection expose only capabilities common to every route.
        values = [adapter_capabilities(route.adapter, route.model or model) for route in self._routes]
        if not all(value.declared for value in values):
            return ProviderCapabilities(declared=False)
        context_values = [value.context_window_tokens for value in values]
        context_window = (
            min(value for value in context_values if value is not None)
            if context_values and all(value is not None for value in context_values)
            else None
        )
        families = {value.continuation_family for value in values}
        return ProviderCapabilities(
            tools=all(value.tools for value in values),
            structured_output=all(value.structured_output for value in values),
            continuation=all(value.continuation for value in values),
            streaming=all(value.streaming for value in values),
            request_timeout=all(value.request_timeout for value in values),
            context_window_tokens=context_window,
            continuation_family=(families.pop() if len(families) == 1 else None),
        )

    def _candidate_indexes(self, request: ModelPayload) -> list[int]:
        if self._selected_index is not None:
            return [self._selected_index]
        if request.get("continuation"):
            # Continuation without an already selected provider is ambiguous.
            return []
        return list(range(len(self._routes)))

    def _request_for_route(
        self,
        request: ModelPayload,
        route: ProviderRoute,
        capabilities: ProviderCapabilities,
    ) -> ModelPayload:
        prepared = deepcopy(request)
        if route.model is not None:
            prepared["model"] = route.model
        if self._policy.timeout_seconds is not None:
            extensions = prepared.setdefault("extensions", {})
            runtime = extensions.setdefault(_RUNTIME_EXTENSION, {})
            if not isinstance(runtime, dict):
                raise ProviderMalformedResponseError(
                    route.adapter.provider,
                    detail="reserved Manager runtime extension must be an object",
                )
            runtime["timeout_seconds"] = self._policy.timeout_seconds
            runtime["capability_fingerprint"] = capabilities.fingerprint()
        return prepared

    def _check_capabilities(
        self,
        request: ModelPayload,
        route: ProviderRoute,
        capabilities: ProviderCapabilities,
    ) -> None:
        provider = route.adapter.provider
        if request.get("tools") and (not capabilities.declared or not capabilities.tools):
            raise ProviderUnsupportedCapabilityError(provider, detail="custom tools")
        if request.get("continuation") and (
            not capabilities.declared or not capabilities.continuation
        ):
            raise ProviderUnsupportedCapabilityError(provider, detail="continuation")
        if self._policy.timeout_seconds is not None and (
            not capabilities.declared or not capabilities.request_timeout
        ):
            raise ProviderUnsupportedCapabilityError(provider, detail="request timeout")

    @staticmethod
    def _can_fail_over(error: ProviderAdapterError) -> bool:
        # Capability/context differences may justify trying a different provider
        # for an initial turn, but they never justify retrying the same provider.
        if error.category in {"unsupported_capability", "context_limit"}:
            return True
        return bool(error.retryable) and error.category in {
            "rate_limit",
            "timeout",
            "transient_unavailable",
            "provider_internal_error",
        }

    def _failed_response(
        self,
        request: ModelPayload,
        error: ProviderAdapterError,
        attempts: list[dict[str, object]],
    ) -> ModelPayload:
        return {
            "response_id": None,
            "provider": self.provider,
            "model": request["model"],
            "status": "failed",
            "output_text": "",
            "tool_proposals": [],
            "usage": {"input_tokens": None, "output_tokens": None, "total_tokens": None},
            "extensions": {
                _RUNTIME_EXTENSION: {
                    "provider_error": {
                        "category": error.category,
                        "retryable": bool(error.retryable),
                    },
                    "attempts": attempts,
                }
            },
        }

    def generate(self, request: ModelPayload) -> ModelPayload:
        validate_model_request(request)
        candidate_indexes = self._candidate_indexes(request)
        if not candidate_indexes:
            error = ProviderUnsupportedCapabilityError(
                self.provider,
                detail="continuation requires a pinned provider route",
            )
            return self._failed_response(request, error, [])

        attempts: list[dict[str, object]] = []
        last_error: ProviderAdapterError | None = None
        for route_position, index in enumerate(candidate_indexes):
            route = self._routes[index]
            provider = route.adapter.provider
            route_model = route.model or request["model"]
            try:
                capabilities = adapter_capabilities(route.adapter, route_model)
                self._check_capabilities(request, route, capabilities)
                prepared = self._request_for_route(request, route, capabilities)
            except Exception as exc:
                error = normalize_provider_exception(provider, exc)
                attempts.append(
                    {
                        "provider": provider,
                        "model": route_model,
                        "attempt": 0,
                        "outcome": error.category,
                    }
                )
                last_error = error
                if self._selected_index is not None or request.get("continuation"):
                    break
                if not self._can_fail_over(error):
                    break
                continue

            for attempt in range(1, self._policy.max_attempts + 1):
                try:
                    response = route.adapter.generate(prepared)
                    validate_model_response(response, expected_provider=provider)
                except Exception as exc:
                    error = normalize_provider_exception(provider, exc)
                    attempts.append(
                        {
                            "provider": provider,
                            "model": route_model,
                            "attempt": attempt,
                            "outcome": error.category,
                        }
                    )
                    last_error = error
                    if error.retryable and attempt < self._policy.max_attempts:
                        delay = self._policy.backoff_seconds(attempt)
                        if delay:
                            self._sleep(delay)
                        continue
                    break

                self._selected_index = index
                normalized = deepcopy(response)
                extensions = normalized.setdefault("extensions", {})
                runtime = extensions.setdefault(_RUNTIME_EXTENSION, {})
                if not isinstance(runtime, dict):
                    error = ProviderMalformedResponseError(
                        provider,
                        detail="reserved Manager runtime response extension must be an object",
                    )
                    attempts.append(
                        {
                            "provider": provider,
                            "model": route_model,
                            "attempt": attempt,
                            "outcome": error.category,
                        }
                    )
                    last_error = error
                    break
                runtime["resilience"] = {
                    "selected_provider": provider,
                    "selected_model": normalized["model"],
                    "capability_fingerprint": capabilities.fingerprint(),
                    "attempt_count": len(attempts) + 1,
                    "failover_used": route_position > 0,
                }
                attempts.append(
                    {
                        "provider": provider,
                        "model": route_model,
                        "attempt": attempt,
                        "outcome": "success",
                    }
                )
                runtime["attempts"] = deepcopy(attempts)
                validate_model_response(normalized, expected_provider=provider)
                return normalized

            if last_error is None:
                last_error = ProviderMalformedResponseError(provider)
            if self._selected_index is not None or request.get("continuation"):
                break
            if not self._can_fail_over(last_error):
                break

        if last_error is None:
            last_error = ProviderUnsupportedCapabilityError(self.provider)
        # Pin the failure identity only when continuation was already pinned.
        return self._failed_response(request, last_error, attempts)
