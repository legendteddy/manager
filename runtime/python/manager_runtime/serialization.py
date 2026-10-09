from __future__ import annotations

import json
import math
from typing import Any

TRUNCATION_MARKER = "...[truncated by Manager]"
_MAX_DEPTH = 64
_MAX_INTEGER_BITS = 4096
_STRICT_MAX_BYTES = 1024 * 1024
_STRICT_MAX_ITEMS = 10_000
_STRICT_MAX_DEPTH = 32


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
    value_type = type(value)
    if value_type is str:
        return value
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value_type is int:
        if value.bit_length() <= _MAX_INTEGER_BITS:
            return str(value)
        return f"<int:{value.bit_length()}-bits>"
    if value_type is float:
        return json.dumps(value, allow_nan=True)
    return f"<{value_type.__name__}>"


def _emit(value: Any, writer: _BoundedWriter, seen: set[int], depth: int) -> None:
    if writer.truncated:
        return
    if depth > _MAX_DEPTH:
        _emit_string("<max-depth>", writer)
        writer.truncated = True
        return

    value_type = type(value)
    if value is None:
        writer.append("null")
        return
    if value is True:
        writer.append("true")
        return
    if value is False:
        writer.append("false")
        return
    if value_type is str:
        _emit_string(value, writer)
        return
    if value_type is int:
        if value.bit_length() > _MAX_INTEGER_BITS:
            _emit_string(f"<int:{value.bit_length()}-bits>", writer)
        else:
            writer.append(str(value))
        return
    if value_type is float:
        writer.append(json.dumps(value, allow_nan=True))
        return
    if value_type is bytes:
        _emit_string(f"<bytes:{len(value)}>", writer)
        return

    if value_type is dict:
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

    if value_type in {list, tuple}:
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

    _emit_string(f"<{value_type.__name__}>", writer)


def bounded_json_text(value: Any, max_chars: int) -> str:
    if type(max_chars) is not int or max_chars < 1:
        raise ValueError("max_chars must be a positive integer")
    writer = _BoundedWriter(max_chars)
    _emit(value, writer, set(), 0)
    return writer.render()


def strict_json_snapshot(
    value: Any,
    *,
    label: str = "value",
    max_bytes: int = _STRICT_MAX_BYTES,
    max_items: int = _STRICT_MAX_ITEMS,
    max_depth: int = _STRICT_MAX_DEPTH,
) -> Any:
    for name, limit in (
        ("max_bytes", max_bytes),
        ("max_items", max_items),
        ("max_depth", max_depth),
    ):
        if type(limit) is not int or limit < 1:
            raise ValueError(f"{name} must be a positive integer")

    item_count = 0
    minimum_chars = 0
    path: set[int] = set()

    def account(amount: int) -> None:
        nonlocal minimum_chars
        minimum_chars += amount
        if minimum_chars > max_bytes:
            raise ValueError(f"{label} exceeds the JSON byte limit")

    def copy_value(current: Any, depth: int) -> Any:
        nonlocal item_count
        if depth > max_depth:
            raise ValueError(f"{label} exceeds the JSON depth limit")
        item_count += 1
        if item_count > max_items:
            raise ValueError(f"{label} exceeds the JSON item limit")

        current_type = type(current)
        if current is None:
            account(4)
            return None
        if current_type is bool:
            account(4 if current else 5)
            return current
        if current_type is str:
            if len(current) > max_bytes:
                raise ValueError(f"{label} exceeds the JSON byte limit")
            account(len(current) + 2)
            return current
        if current_type is int:
            if current.bit_length() > _MAX_INTEGER_BITS:
                raise ValueError(f"{label} contains an oversized integer")
            account(1)
            return current
        if current_type is float:
            if not math.isfinite(current):
                raise ValueError(f"{label} contains a non-finite number")
            account(1)
            return current
        if current_type is dict:
            identity = id(current)
            if identity in path:
                raise ValueError(f"{label} contains a cycle")
            path.add(identity)
            try:
                account(2)
                copied: dict[str, Any] = {}
                first = True
                for key, item in current.items():
                    if type(key) is not str:
                        raise TypeError(f"{label} object keys must be strings")
                    if len(key) > max_bytes:
                        raise ValueError(f"{label} exceeds the JSON byte limit")
                    account(len(key) + 3 + (0 if first else 1))
                    first = False
                    copied[key] = copy_value(item, depth + 1)
                return copied
            finally:
                path.remove(identity)
        if current_type is list:
            identity = id(current)
            if identity in path:
                raise ValueError(f"{label} contains a cycle")
            path.add(identity)
            try:
                account(2)
                copied_list: list[Any] = []
                for index, item in enumerate(current):
                    if index:
                        account(1)
                    copied_list.append(copy_value(item, depth + 1))
                return copied_list
            finally:
                path.remove(identity)
        raise TypeError(f"{label} contains a non-JSON value")

    snapshot = copy_value(value, 0)
    try:
        encoded = json.dumps(
            snapshot,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
            check_circular=True,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError(f"{label} must be strict JSON") from exc
    if len(encoded) > max_bytes:
        raise ValueError(f"{label} exceeds the JSON byte limit")
    return snapshot
