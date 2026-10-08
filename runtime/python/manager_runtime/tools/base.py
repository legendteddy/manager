from __future__ import annotations

import hashlib
import json
import math
import re
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

_SCHEMA_TYPES = {"string", "integer", "number", "boolean", "object", "array", "null"}
_SCHEMA_ANNOTATIONS = {
    "title",
    "description",
    "default",
    "examples",
    "deprecated",
    "readOnly",
    "writeOnly",
}
_SCHEMA_CONSTRAINTS = {
    "type",
    "properties",
    "required",
    "additionalProperties",
    "enum",
    "const",
    "items",
    "minLength",
    "maxLength",
    "pattern",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minItems",
    "maxItems",
    "uniqueItems",
    "minProperties",
    "maxProperties",
}
_SUPPORTED_SCHEMA_KEYS = _SCHEMA_ANNOTATIONS | _SCHEMA_CONSTRAINTS


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


def _schema_types(value: Any, path: str) -> list[str]:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list) and value and all(isinstance(item, str) for item in value):
        values = value
    else:
        raise TypeError(f"tool input schema {path}.type must be a string or non-empty string array")
    unknown = [item for item in values if item not in _SCHEMA_TYPES]
    if unknown:
        raise ValueError(f"tool input schema {path}.type contains unsupported values: {', '.join(unknown)}")
    if len(set(values)) != len(values):
        raise ValueError(f"tool input schema {path}.type must not contain duplicates")
    return values


def validate_supported_schema(schema: dict[str, Any], *, path: str = "$") -> None:
    """Reject schema features this zero-dependency runtime cannot enforce.

    Manager must never advertise a constraint and then silently ignore it at the
    execution boundary. This validator intentionally supports a conservative
    JSON-Schema subset and fails closed for every other keyword.
    """
    if not isinstance(schema, dict):
        raise TypeError(f"tool input schema {path} must be an object")

    unsupported = sorted(set(schema) - _SUPPORTED_SCHEMA_KEYS)
    if unsupported:
        raise ValueError(
            f"tool input schema {path} uses unsupported keywords: {', '.join(unsupported)}"
        )

    if "type" in schema:
        _schema_types(schema["type"], path)

    properties = schema.get("properties")
    if properties is not None:
        if not isinstance(properties, dict):
            raise TypeError(f"tool input schema {path}.properties must be an object")
        for key, rule in properties.items():
            if not isinstance(key, str):
                raise TypeError(f"tool input schema {path}.properties keys must be strings")
            if not isinstance(rule, dict):
                raise TypeError(f"tool input schema {path}.properties.{key} must be an object")
            validate_supported_schema(rule, path=f"{path}.{key}")

    required = schema.get("required")
    if required is not None:
        if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
            raise TypeError(f"tool input schema {path}.required must be a string array")
        if len(set(required)) != len(required):
            raise ValueError(f"tool input schema {path}.required must not contain duplicates")
        if properties is not None:
            unknown_required = [item for item in required if item not in properties]
            if unknown_required:
                raise ValueError(
                    f"tool input schema {path}.required references unknown properties: {', '.join(unknown_required)}"
                )

    if "additionalProperties" in schema and not isinstance(schema["additionalProperties"], bool):
        raise TypeError(
            f"tool input schema {path}.additionalProperties must be boolean in the reference runtime"
        )

    if "items" in schema:
        if not isinstance(schema["items"], dict):
            raise TypeError(f"tool input schema {path}.items must be an object")
        validate_supported_schema(schema["items"], path=f"{path}[]")

    if "enum" in schema:
        enum = schema["enum"]
        if not isinstance(enum, list) or not enum:
            raise TypeError(f"tool input schema {path}.enum must be a non-empty array")

    for key in ("minLength", "maxLength", "minItems", "maxItems", "minProperties", "maxProperties"):
        if key in schema:
            value = schema[key]
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise TypeError(f"tool input schema {path}.{key} must be a non-negative integer")

    for minimum_key, maximum_key in (
        ("minLength", "maxLength"),
        ("minItems", "maxItems"),
        ("minProperties", "maxProperties"),
    ):
        if minimum_key in schema and maximum_key in schema and schema[minimum_key] > schema[maximum_key]:
            raise ValueError(f"tool input schema {path} has {minimum_key} greater than {maximum_key}")

    if "pattern" in schema:
        if not isinstance(schema["pattern"], str):
            raise TypeError(f"tool input schema {path}.pattern must be text")
        try:
            re.compile(schema["pattern"])
        except re.error as exc:
            raise ValueError(f"tool input schema {path}.pattern is invalid: {exc}") from exc

    for key in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf"):
        if key in schema:
            value = schema[key]
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
                raise TypeError(f"tool input schema {path}.{key} must be a finite number")
    if "multipleOf" in schema and schema["multipleOf"] <= 0:
        raise ValueError(f"tool input schema {path}.multipleOf must be greater than zero")

    if "uniqueItems" in schema and not isinstance(schema["uniqueItems"], bool):
        raise TypeError(f"tool input schema {path}.uniqueItems must be boolean")


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
    if definition["input_schema"].get("type") != "object":
        raise ValueError("tool input_schema root type must be object")
    validate_supported_schema(definition["input_schema"])
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
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and (not isinstance(value, float) or math.isfinite(value))
        )
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "null":
        return value is None
    return False


def _validate_value(value: Any, schema: dict[str, Any], path: str) -> None:
    expected_type = schema.get("type")
    if isinstance(expected_type, str) and not _matches_type(value, expected_type):
        raise TypeError(f"tool argument {path} does not match type {expected_type!r}")
    if isinstance(expected_type, list) and not any(
        _matches_type(value, item) for item in expected_type
    ):
        raise TypeError(f"tool argument {path} does not match allowed types")

    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"tool argument {path} is outside its enum")
    if "const" in schema and value != schema["const"]:
        raise ValueError(f"tool argument {path} does not match its const value")

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            raise ValueError(f"tool argument {path} is shorter than minLength")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise ValueError(f"tool argument {path} is longer than maxLength")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            raise ValueError(f"tool argument {path} does not match pattern")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"tool argument {path} must be a finite number")
        if "minimum" in schema and value < schema["minimum"]:
            raise ValueError(f"tool argument {path} is below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise ValueError(f"tool argument {path} is above maximum")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            raise ValueError(f"tool argument {path} is not above exclusiveMinimum")
        if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
            raise ValueError(f"tool argument {path} is not below exclusiveMaximum")
        if "multipleOf" in schema:
            quotient = value / schema["multipleOf"]
            if not math.isclose(quotient, round(quotient), rel_tol=1e-12, abs_tol=1e-12):
                raise ValueError(f"tool argument {path} is not a multipleOf the required value")

    if isinstance(value, dict):
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        missing = [key for key in required if key not in value]
        if missing:
            raise ValueError(f"tool arguments missing required fields at {path}: {', '.join(missing)}")
        if schema.get("additionalProperties") is False:
            unexpected = [key for key in value if key not in properties]
            if unexpected:
                raise ValueError(
                    f"tool arguments contain unexpected fields at {path}: {', '.join(unexpected)}"
                )
        if "minProperties" in schema and len(value) < schema["minProperties"]:
            raise ValueError(f"tool argument {path} has fewer than minProperties")
        if "maxProperties" in schema and len(value) > schema["maxProperties"]:
            raise ValueError(f"tool argument {path} has more than maxProperties")
        for key, item in value.items():
            rule = properties.get(key)
            if isinstance(rule, dict):
                _validate_value(item, rule, f"{path}.{key}")

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            raise ValueError(f"tool argument {path} has fewer than minItems")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise ValueError(f"tool argument {path} has more than maxItems")
        if schema.get("uniqueItems"):
            for index, item in enumerate(value):
                if any(item == previous for previous in value[:index]):
                    raise ValueError(f"tool argument {path} must contain unique items")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _validate_value(item, item_schema, f"{path}[{index}]")


def validate_arguments(arguments: dict[str, Any], schema: dict[str, Any]) -> None:
    """Validate arguments against the exact schema subset accepted at registration.

    Any unsupported schema feature is rejected when the tool is registered, so
    every constraint a registered tool advertises is enforced here before its
    adapter can execute.
    """
    validate_supported_schema(schema)
    _validate_value(arguments, schema, "$")
