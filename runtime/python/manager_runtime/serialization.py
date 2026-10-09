from __future__ import annotations

import json
import math
from typing import Any

TRUNCATION_MARKER = "...[truncated by Manager]"
_MAX_DEPTH = 64
_MAX_INTEGER_BITS = 4096


class _BoundedWriter:
    def __init__(self, max_chars: int) -> None:
        self.max_chars = max_chars
        self.parts: list[str] = []
        self.length = 0
        self.truncated = False

    @property
    def remaining(self) -> int:
        return max(0, self.max_chars - self.length)

    def append(self, text: str) -> bool:
        if self.truncated:
            return False
        remaining = self.remaining
        if remaining <= 0:
            self.truncated = True
            return False
        if len(text) <= remaining:
            self.parts.append(text)
            self.length += len(text)
            return True
        self.parts.append(text[:remaining])
        self.length += remaining
        self.truncated = True
        return False

    def render(self) -> str:
        text = "".join(self.parts)
        if not self.truncated:
            return text
        if self.max_chars <= len(TRUNCATION_MARKER):
            return TRUNCATION_MARKER[: self.max_chars]
        return text[: self.max_chars - len(TRUNCATION_MARKER)] + TRUNCATION_MARKER


def _escaped_character(char: str) -> str:
    escapes = {
        '"': '\\"',
        "\\": "\\\\",
        "\b": "\\b",
        "\f": "\\f",
        "\n": "\\n",
        "\r": "\\r",
        "\t": "\\t",
    }
    if char in escapes:
        return escapes[char]
    codepoint = ord(char)
    if codepoint < 0x20 or 0xD800 <= codepoint <= 0xDFFF:
        return f"\\u{codepoint:04x}"
    return char


def _emit_string(value: str, writer: _BoundedWriter) -> None:
    if not writer.append('"'):
        return
    for char in value:
        if not writer.append(_escaped_character(char)):
            return
    writer.append('"')


def _key_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int) and not isinstance(value, bool):
        if value.bit_length() <= _MAX_INTEGER_BITS:
            return str(value)
        return f"<int:{value.bit_length()}-bits>"
    if isinstance(value, float):
        return json.dumps(value, allow_nan=True)
    return f"<{type(value).__name__}>"


def _emit(value: Any, writer: _BoundedWriter, seen: set[int], depth: int) -> None:
    if writer.truncated:
        return
    if depth > _MAX_DEPTH:
        _emit_string("<max-depth>", writer)
        writer.truncated = True
        return

    if value is None:
        writer.append("null")
        return
    if value is True:
        writer.append("true")
        return
    if value is False:
        writer.append("false")
        return
    if isinstance(value, str):
        _emit_string(value, writer)
        return
    if isinstance(value, int) and not isinstance(value, bool):
        if value.bit_length() > _MAX_INTEGER_BITS:
            _emit_string(f"<int:{value.bit_length()}-bits>", writer)
        else:
            writer.append(str(value))
        return
    if isinstance(value, float):
        writer.append(json.dumps(value, allow_nan=True))
        return
    if isinstance(value, bytes):
        _emit_string(f"<bytes:{len(value)}>", writer)
        return

    if isinstance(value, dict):
        identity = id(value)
        if identity in seen:
            _emit_string("<cycle>", writer)
            return
        seen.add(identity)
        try:
            if not writer.append("{"):
                return
            first = True
            for key, item in value.items():
                if writer.truncated:
                    break
                if not first and not writer.append(","):
                    break
                first = False
                _emit_string(_key_text(key), writer)
                if not writer.append(":"):
                    break
                _emit(item, writer, seen, depth + 1)
            writer.append("}")
        finally:
            seen.remove(identity)
        return

    if isinstance(value, (list, tuple)):
        identity = id(value)
        if identity in seen:
            _emit_string("<cycle>", writer)
            return
        seen.add(identity)
        try:
            if not writer.append("["):
                return
            first = True
            for item in value:
                if writer.truncated:
                    break
                if not first and not writer.append(","):
                    break
                first = False
                _emit(item, writer, seen, depth + 1)
            writer.append("]")
        finally:
            seen.remove(identity)
        return

    # Do not call arbitrary __str__ or __repr__ implementations here. Tool
    # outputs are an untrusted resource boundary and such methods may allocate
    # without bound, block, or expose sensitive implementation details.
    _emit_string(f"<{type(value).__name__}>", writer)


def bounded_json_text(value: Any, max_chars: int) -> str:
    """Return a bounded JSON-like preview without materializing the full value.

    Traversal stops as soon as the output budget is exhausted. The function is
    intentionally a model-context preview, not a canonical serializer or a
    replacement for persistence formats.
    """
    if not isinstance(max_chars, int) or isinstance(max_chars, bool) or max_chars < 1:
        raise ValueError("max_chars must be a positive integer")
    writer = _BoundedWriter(max_chars)
    _emit(value, writer, set(), 0)
    return writer.render()


def strict_json_snapshot(value: Any, *, label: str = "value") -> Any:
    """Return a detached strict-JSON snapshot or fail closed."""
    stack = [value]
    seen_containers: set[int] = set()
    while stack:
        current = stack.pop()
        current_type = type(current)
        if current is None or current_type in {bool, str, int}:
            continue
        if current_type is float:
            if not math.isfinite(current):
                raise ValueError(f"{label} contains a non-finite number")
            continue
        if current_type is dict:
            identity = id(current)
            if identity in seen_containers:
                continue
            seen_containers.add(identity)
            for key, item in current.items():
                if type(key) is not str:
                    raise TypeError(f"{label} object keys must be strings")
                stack.append(item)
            continue
        if current_type is list:
            identity = id(current)
            if identity in seen_containers:
                continue
            seen_containers.add(identity)
            stack.extend(current)
            continue
        raise TypeError(f"{label} contains a non-JSON value")

    try:
        encoded = json.dumps(
            value,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
            check_circular=True,
        )
        return json.loads(encoded)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError(f"{label} must be strict JSON") from exc
