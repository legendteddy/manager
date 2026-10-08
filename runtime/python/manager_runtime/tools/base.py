from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

ToolPayload = dict[str, Any]

SIDE_EFFECT_CLASSES = {
    "analysis",
    "read",
    "reversible_write",
    "external_commitment",
    "sensitive_destructive",
}
CONSEQUENTIAL_CLASSES = {
    "reversible_write",
    "external_commitment",
    "sensitive_destructive",
}


class ToolRuntimeError(RuntimeError):
    """Raised when a tool cannot be safely registered or executed."""


@runtime_checkable
class ToolAdapter(Protocol):
    """Application-owned implementation of a registered tool."""

    def execute(self, arguments: dict[str, Any]) -> Any:
        """Perform the tool operation."""


@dataclass(frozen=True)
class RegisteredTool:
    definition: ToolPayload
    adapter: ToolAdapter


class ToolRegistry:
    """Trusted registry for tool metadata and implementations.

    Side-effect metadata is owned by this registry and is never taken from a
    model-generated proposal.
    """

    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(self, definition: ToolPayload, adapter: ToolAdapter) -> None:
        validate_tool_definition(definition)
        name = definition["name"]
        if name in self._tools:
            raise ToolRuntimeError(f"tool already registered: {name}")
        self._tools[name] = RegisteredTool(dict(definition), adapter)

    def get(self, name: str) -> RegisteredTool | None:
        return self._tools.get(name)

    def model_definitions(self, allowed_tools: list[str] | None = None) -> list[ToolPayload]:
        names = allowed_tools if allowed_tools is not None else sorted(self._tools)
        definitions: list[ToolPayload] = []
        for name in names:
            registered = self._tools.get(name)
            if registered is None:
                raise ToolRuntimeError(f"unknown allowed tool: {name}")
            definition = registered.definition
            definitions.append(
                {
                    "name": definition["name"],
                    "description": definition["description"],
                    "input_schema": definition["input_schema"],
                }
            )
        return definitions


def _stable_digest(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def tool_request_fingerprint(request: ToolPayload) -> str:
    """Bind approval to the exact tool identity, target, and arguments."""
    return _stable_digest(
        {
            "tool_name": request["tool_name"],
            "target": request.get("target"),
            "arguments": request["arguments"],
        }
    )


def tool_definition_fingerprint(definition: ToolPayload) -> str:
    """Identify policy-relevant tool metadata reviewed at checkpoint time."""
    return _stable_digest(
        {
            "name": definition["name"],
            "version": definition.get("version"),
            "side_effect_class": definition["side_effect_class"],
            "input_schema": definition["input_schema"],
            "requires_verification": definition["requires_verification"],
            "sensitive_output": bool(definition.get("sensitive_output", False)),
        }
    )


def validate_tool_definition(definition: ToolPayload) -> None:
    required = (
        "name",
        "description",
        "side_effect_class",
        "input_schema",
        "requires_verification",
    )
    missing = [key for key in required if key not in definition]
    if missing:
        raise ValueError(f"tool definition missing required fields: {', '.join(missing)}")
    if not isinstance(definition["name"], str) or not definition["name"]:
        raise TypeError("tool name must be non-empty text")
    if definition["side_effect_class"] not in SIDE_EFFECT_CLASSES:
        raise ValueError("tool side_effect_class is not normalized")
    if not isinstance(definition["input_schema"], dict):
        raise TypeError("tool input_schema must be an object")
    if not isinstance(definition["requires_verification"], bool):
        raise TypeError("requires_verification must be boolean")
    if (
        definition["side_effect_class"] in CONSEQUENTIAL_CLASSES
        and not definition["requires_verification"]
    ):
        raise ValueError("consequential tools must require verification")
    if definition["side_effect_class"] in CONSEQUENTIAL_CLASSES:
        version = definition.get("version")
        if not isinstance(version, str) or not version.strip():
            raise ValueError("consequential tools must declare a non-empty version")


def validate_tool_request(request: ToolPayload) -> None:
    required = ("request_id", "run_id", "tool_name", "arguments", "proposed_by")
    missing = [key for key in required if key not in request]
    if missing:
        raise ValueError(f"tool request missing required fields: {', '.join(missing)}")
    if not isinstance(request["arguments"], dict):
        raise TypeError("tool request arguments must be an object")
    if request["proposed_by"] not in {"model", "primary_agent", "human", "system"}:
        raise ValueError("tool request proposed_by is not normalized")


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "null":
        return value is None
    return True


def validate_arguments(arguments: dict[str, Any], schema: dict[str, Any]) -> None:
    """Validate the small JSON-Schema subset used by the reference runtime.

    Full JSON Schema validation remains outside this zero-dependency reference
    implementation. The supported subset covers required fields, primitive
    types, enums, and additionalProperties=false.
    """
    required = schema.get("required", [])
    missing = [key for key in required if key not in arguments]
    if missing:
        raise ValueError(f"tool arguments missing required fields: {', '.join(missing)}")

    properties = schema.get("properties", {})
    if schema.get("additionalProperties") is False:
        unexpected = [key for key in arguments if key not in properties]
        if unexpected:
            raise ValueError(f"tool arguments contain unexpected fields: {', '.join(unexpected)}")

    for key, value in arguments.items():
        rule = properties.get(key)
        if not isinstance(rule, dict):
            continue
        expected_type = rule.get("type")
        if isinstance(expected_type, str) and not _matches_type(value, expected_type):
            raise TypeError(f"tool argument {key!r} does not match type {expected_type!r}")
        if isinstance(expected_type, list) and not any(
            _matches_type(value, item) for item in expected_type if isinstance(item, str)
        ):
            raise TypeError(f"tool argument {key!r} does not match allowed types")
        if "enum" in rule and value not in rule["enum"]:
            raise ValueError(f"tool argument {key!r} is outside its enum")
