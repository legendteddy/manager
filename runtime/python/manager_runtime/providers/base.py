from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from typing import Any, Protocol, runtime_checkable

ModelPayload = dict[str, Any]

_TOOL_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
_MODEL_REQUEST_KEYS = {
    "request_id", "model", "instructions", "input", "max_output_tokens",
    "tools", "continuation", "metadata", "extensions",
}
_MODEL_REQUEST_TOOL_KEYS = {"name", "description", "input_schema"}
_CONTINUATION_KEYS = {"prior_response_ref", "tool_results"}
_CONTINUATION_RESULT_KEYS = {"proposal_id", "status", "output", "redacted"}
_MODEL_RESPONSE_KEYS = {
    "response_id", "provider", "model", "status", "output_text",
    "tool_proposals", "usage", "extensions",
}
_TOOL_PROPOSAL_KEYS = {
    "proposal_id", "tool_name", "arguments", "target", "source_ref",
}
_USAGE_KEYS = {"input_tokens", "output_tokens", "total_tokens"}
_MAX_MODEL_JSON_DEPTH = 64
_MAX_MODEL_JSON_NODES = 100_000


@dataclass(frozen=True)
class ProviderCapabilities:
    """Capabilities implemented by one provider/model adapter boundary."""

    tools: bool = False
    structured_output: bool = False
    continuation: bool = False
    streaming: bool = False
    request_timeout: bool = False
    context_window_tokens: int | None = None
    continuation_family: str | None = None
    declared: bool = True

    def __post_init__(self) -> None:
        for name in ("tools", "structured_output", "continuation", "streaming", "request_timeout", "declared"):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"provider capability {name} must be boolean")
        if self.context_window_tokens is not None and (
            not isinstance(self.context_window_tokens, int)
            or isinstance(self.context_window_tokens, bool)
            or self.context_window_tokens < 1
        ):
            raise TypeError("provider context_window_tokens must be a positive integer or null")
        if self.continuation_family is not None and (
            not isinstance(self.continuation_family, str) or not self.continuation_family
        ):
            raise TypeError("provider continuation_family must be non-empty text or null")

    def fingerprint(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ProviderAdapterError(RuntimeError):
    """Sanitized model-provider boundary error."""

    category = "provider_internal_error"
    retryable = False

    def __init__(self, provider: str, *, operation: str = "generate", detail: str | None = None) -> None:
        if not isinstance(provider, str) or not provider:
            provider = "unknown-provider"
        self.provider = provider
        self.operation = operation
        self.detail = detail
        suffix = f": {detail}" if detail else ""
        super().__init__(f"{provider} {operation} failed ({self.category}){suffix}")


class ProviderAuthenticationError(ProviderAdapterError):
    category = "authentication_failure"


class ProviderAuthorizationError(ProviderAdapterError):
    category = "authorization_failure"


class ProviderRateLimitError(ProviderAdapterError):
    category = "rate_limit"
    retryable = True


class ProviderTimeoutError(ProviderAdapterError):
    category = "timeout"
    retryable = True


class ProviderUnavailableError(ProviderAdapterError):
    category = "transient_unavailable"
    retryable = True


class ProviderMalformedResponseError(ProviderAdapterError):
    category = "malformed_response"


class ProviderUnsupportedCapabilityError(ProviderAdapterError):
    category = "unsupported_capability"


class ProviderContextLimitError(ProviderAdapterError):
    category = "context_limit"


class ProviderInternalError(ProviderAdapterError):
    category = "provider_internal_error"

    def __init__(
        self,
        provider: str,
        *,
        operation: str = "generate",
        detail: str | None = None,
        retryable: bool = False,
    ) -> None:
        self.retryable = retryable
        super().__init__(provider, operation=operation, detail=detail)


@runtime_checkable
class ModelAdapter(Protocol):
    provider: str

    def generate(self, request: ModelPayload) -> ModelPayload:
        """Generate a normalized model response for a normalized request."""


def adapter_capabilities(adapter: ModelAdapter, model: str) -> ProviderCapabilities:
    getter = getattr(adapter, "get_capabilities", None)
    value = getter(model) if callable(getter) else getattr(adapter, "capabilities", None)
    if value is None:
        return ProviderCapabilities(declared=False)
    if not isinstance(value, ProviderCapabilities):
        raise TypeError("adapter capabilities must be ProviderCapabilities")
    return value


def normalize_provider_exception(provider: str, exc: Exception) -> ProviderAdapterError:
    if isinstance(exc, ProviderAdapterError):
        return exc
    if isinstance(exc, TimeoutError):
        return ProviderTimeoutError(provider)
    if isinstance(exc, (TypeError, ValueError, KeyError, json.JSONDecodeError)):
        return ProviderMalformedResponseError(provider)
    return ProviderInternalError(provider)


def _validate_json_value(
    value: Any,
    *,
    path: str,
    depth: int = 0,
    nodes: list[int] | None = None,
) -> None:
    if depth > _MAX_MODEL_JSON_DEPTH:
        raise ValueError(f"{path} exceeds maximum JSON nesting depth of {_MAX_MODEL_JSON_DEPTH}")
    budget = nodes if nodes is not None else [0]
    budget[0] += 1
    if budget[0] > _MAX_MODEL_JSON_NODES:
        raise ValueError(f"{path} exceeds maximum JSON node count of {_MAX_MODEL_JSON_NODES}")

    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite number")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_value(item, path=f"{path}[{index}]", depth=depth + 1, nodes=budget)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{path} object keys must be strings")
            _validate_json_value(item, path=f"{path}.{key}", depth=depth + 1, nodes=budget)
        return
    raise TypeError(f"{path} must contain only JSON-compatible values")


def _validate_tool_definitions(tools: Any) -> None:
    if not isinstance(tools, list):
        raise TypeError("model request tools must be a list")
    seen_payloads: set[str] = set()
    seen_names: set[str] = set()
    for tool in tools:
        if not isinstance(tool, dict):
            raise TypeError("model request tool definitions must be objects")
        unknown = sorted(set(tool) - _MODEL_REQUEST_TOOL_KEYS)
        if unknown:
            raise ValueError(f"model request tool definition has unknown fields: {', '.join(unknown)}")
        for key in ("name", "description", "input_schema"):
            if key not in tool:
                raise ValueError(f"model request tool definition missing {key}")
        name = tool["name"]
        if not isinstance(name, str) or not _TOOL_NAME.fullmatch(name):
            raise ValueError("model request tool name is invalid")
        if name in seen_names:
            raise ValueError("model request tool names must be unique")
        seen_names.add(name)
        description = tool["description"]
        if not isinstance(description, str) or not description:
            raise ValueError("model request tool description must be non-empty text")
        if not isinstance(tool["input_schema"], dict):
            raise TypeError("model request tool input_schema must be an object")
        _validate_json_value(tool, path=f"model request tool {name}")
        try:
            encoded = json.dumps(tool, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
        except (TypeError, ValueError, RecursionError) as exc:
            raise TypeError("model request tool definitions must be JSON-compatible") from exc
        if encoded in seen_payloads:
            raise ValueError("model request tool definitions must be unique")
        seen_payloads.add(encoded)


def _validate_continuation(continuation: Any) -> None:
    if not isinstance(continuation, dict):
        raise TypeError("model request continuation must be an object")
    unknown = sorted(set(continuation) - _CONTINUATION_KEYS)
    if unknown:
        raise ValueError(f"model request continuation has unknown fields: {', '.join(unknown)}")
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
        unknown_result = sorted(set(result) - _CONTINUATION_RESULT_KEYS)
        if unknown_result:
            raise ValueError(f"continuation tool result has unknown fields: {', '.join(unknown_result)}")
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
    if not isinstance(request, dict):
        raise TypeError("model request must be an object")
    _validate_json_value(request, path="model request")
    required = ("request_id", "model", "input")
    missing = [key for key in required if key not in request]
    if missing:
        raise ValueError(f"model request missing required fields: {', '.join(missing)}")
    unknown = sorted(set(request) - _MODEL_REQUEST_KEYS)
    if unknown:
        raise ValueError(f"model request has unknown fields: {', '.join(unknown)}")
    for key in required:
        value = request[key]
        if not isinstance(value, str) or not value:
            raise ValueError(f"model request {key} must be non-empty text")
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
    for key in ("metadata", "extensions"):
        if key in request and not isinstance(request[key], dict):
            raise TypeError(f"model request {key} must be an object")


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


def _validate_tool_proposals(proposals: Any, *, response_status: str, response_id: str | None) -> None:
    if not isinstance(proposals, list):
        raise TypeError("model response tool_proposals must be a list")
    if response_status != "completed" and proposals:
        raise ValueError("non-completed model responses must not contain tool proposals")
    if proposals and not response_id:
        raise ValueError("tool proposals require a provider response identifier")
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
        _validate_json_value(proposal["arguments"], path=f"tool proposal {proposal_id} arguments")
        for key in ("target", "source_ref"):
            if key in proposal and proposal[key] is not None and not isinstance(proposal[key], str):
                raise TypeError(f"tool proposal {key} must be text or null")


def validate_model_response(response: ModelPayload, *, expected_provider: str | None = None) -> None:
    if not isinstance(response, dict):
        raise TypeError("model response must be an object")
    _validate_json_value(response, path="model response")
    required = ("response_id", "provider", "model", "status", "output_text", "usage")
    missing = [key for key in required if key not in response]
    if missing:
        raise ValueError(f"model response missing required fields: {', '.join(missing)}")
    unknown = sorted(set(response) - _MODEL_RESPONSE_KEYS)
    if unknown:
        raise ValueError(f"model response has unknown fields: {', '.join(unknown)}")
    response_id = response["response_id"]
    if response_id is not None and (not isinstance(response_id, str) or not response_id):
        raise TypeError("model response response_id must be non-empty text or null")
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
    _validate_tool_proposals(response.get("tool_proposals", []), response_status=status, response_id=response_id)
    _validate_usage(response["usage"])
    if "extensions" in response and not isinstance(response["extensions"], dict):
        raise TypeError("model response extensions must be an object")
