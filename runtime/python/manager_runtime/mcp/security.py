from __future__ import annotations

import ipaddress
import json
import os
import socket
import threading
from copy import copy, deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from .base import MCPBoundaryError, remote_schema_fingerprint

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


@dataclass(frozen=True)
class MCPResourceLimits:
    """Manager-owned limits for untrusted MCP discovery, requests, and results."""

    max_discovered_tools: int = 128
    max_discovery_pages: int = 32
    max_tool_name_bytes: int = 256
    max_description_bytes: int = 8 * 1024
    max_schema_bytes: int = 128 * 1024
    max_schema_depth: int = 32
    max_schema_items: int = 10_000
    max_request_bytes: int = 256 * 1024
    max_request_depth: int = 32
    max_request_items: int = 10_000
    max_result_bytes: int = 1024 * 1024
    max_result_items: int = 10_000
    max_result_depth: int = 32
    max_stdio_args: int = 128
    max_stdio_argument_bytes: int = 8 * 1024
    max_stdio_env_entries: int = 128
    max_stdio_env_bytes: int = 64 * 1024
    max_stdio_command_bytes: int = 4 * 1024
    max_stdio_cwd_bytes: int = 4 * 1024
    max_url_bytes: int = 8 * 1024
    max_resolved_addresses: int = 16
    max_stderr_bytes: int = 64 * 1024

    def __post_init__(self) -> None:
        for name, value in vars(self).items():
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")


@dataclass(frozen=True)
class MCPNetworkPolicy:
    """Safe-by-default egress policy for URL-backed MCP transports.

    Loopback HTTP remains available for local development and conformance.
    Private/link-local destinations require explicit application policy. Strong
    production egress still belongs in a firewall, proxy, service mesh, or
    container/network sandbox because userspace DNS checks cannot eliminate all
    DNS-rebinding or compromised-host races.
    """

    allow_loopback: bool = True
    allow_private_networks: bool = False
    allow_link_local: bool = False
    allow_multicast: bool = False
    allow_unspecified: bool = False
    allow_plain_http_loopback: bool = True
    allow_plain_http_private: bool = False
    resolve_hostnames: bool = True
    allowed_hosts: tuple[str, ...] = ()
    blocked_hosts: tuple[str, ...] = (
        "metadata.google.internal",
        "metadata.google",
    )


@dataclass(frozen=True)
class MCPStdioPolicy:
    """Application policy for stdio process launch targets."""

    allow_shell_wrappers: bool = False
    allowed_cwd_roots: tuple[str, ...] = ()


class BoundedTextSink:
    """A pipe-backed stderr sink that retains only a bounded diagnostic prefix.

    Subprocess APIs require stderr targets to expose a real file descriptor.
    A background drain thread continuously consumes the pipe so a noisy or
    malicious child cannot block on stderr after Manager reaches the capture
    limit. Bytes beyond the limit are discarded rather than buffered to RAM or
    an unbounded temporary file.
    """

    def __init__(self, max_bytes: int) -> None:
        if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes <= 0:
            raise ValueError("max_bytes must be a positive integer")
        self.max_bytes = max_bytes
        self._parts: list[bytes] = []
        self._captured_bytes = 0
        self.truncated = False
        self._lock = threading.Lock()
        self._read_fd, self._write_fd = os.pipe()
        self._closed = False
        self._drain_thread = threading.Thread(target=self._drain, daemon=True)
        self._drain_thread.start()

    def _capture(self, raw: bytes) -> None:
        with self._lock:
            remaining = self.max_bytes - self._captured_bytes
            if remaining > 0:
                kept = raw[:remaining]
                self._parts.append(kept)
                self._captured_bytes += len(kept)
            if len(raw) > max(remaining, 0):
                self.truncated = True

    def _drain(self) -> None:
        try:
            while True:
                raw = os.read(self._read_fd, 64 * 1024)
                if not raw:
                    return
                self._capture(raw)
        except OSError:
            return
        finally:
            try:
                os.close(self._read_fd)
            except OSError:
                pass

    def fileno(self) -> int:
        if self._closed:
            raise ValueError("I/O operation on closed MCP stderr sink")
        return self._write_fd

    def write(self, value: str) -> int:
        # Retain TextIO-like behavior for direct diagnostics/tests. Subprocess
        # output itself reaches this object through fileno() and the drain pipe.
        text = value if isinstance(value, str) else str(value)
        self._capture(text.encode("utf-8", errors="replace"))
        return len(text)

    def flush(self) -> None:
        return None

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            os.close(self._write_fd)
        except OSError:
            pass
        self._drain_thread.join(timeout=1.0)

    def getvalue(self) -> str:
        with self._lock:
            return b"".join(self._parts).decode("utf-8", errors="replace")


def _json_bytes(value: Any, *, label: str) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise MCPBoundaryError(f"{label} is not JSON-serializable") from exc


def _validate_structure(
    value: Any,
    *,
    label: str,
    max_depth: int,
    max_items: int,
    max_bytes: int,
) -> None:
    stack: list[tuple[Any, int]] = [(value, 0)]
    items = 0
    approximate_bytes = 0
    while stack:
        current, depth = stack.pop()
        if depth > max_depth:
            raise MCPBoundaryError(f"{label} exceeds Manager's nesting limit")
        if isinstance(current, dict):
            items += len(current)
            if items > max_items:
                raise MCPBoundaryError(f"{label} exceeds Manager's item limit")
            for key, item in current.items():
                if not isinstance(key, str):
                    raise MCPBoundaryError(f"{label} object keys must be strings")
                approximate_bytes += len(key.encode("utf-8"))
                if approximate_bytes > max_bytes:
                    raise MCPBoundaryError(f"{label} exceeds Manager's byte limit")
                stack.append((item, depth + 1))
        elif isinstance(current, list):
            items += len(current)
            if items > max_items:
                raise MCPBoundaryError(f"{label} exceeds Manager's item limit")
            for item in current:
                stack.append((item, depth + 1))
        elif current is None or isinstance(current, (int, float, bool)):
            items += 1
            # Strings and object keys dominate attacker-controlled allocation.
            # Count a minimal scalar byte here, then enforce the exact encoded
            # size after traversal to avoid overly conservative false rejects.
            approximate_bytes += 1
        elif isinstance(current, str):
            items += 1
            approximate_bytes += len(current.encode("utf-8"))
        else:
            raise MCPBoundaryError(f"{label} contains a non-JSON value")
        if items > max_items:
            raise MCPBoundaryError(f"{label} exceeds Manager's item limit")
        if approximate_bytes > max_bytes:
            raise MCPBoundaryError(f"{label} exceeds Manager's byte limit")


def bound_json_value(
    value: Any,
    *,
    label: str,
    max_bytes: int,
    max_depth: int,
    max_items: int,
) -> Any:
    """Validate and snapshot an untrusted JSON-like value under explicit bounds."""

    _validate_structure(
        value,
        label=label,
        max_depth=max_depth,
        max_items=max_items,
        max_bytes=max_bytes,
    )
    if len(_json_bytes(value, label=label)) > max_bytes:
        raise MCPBoundaryError(f"{label} exceeds Manager's byte limit")
    return deepcopy(value)


def validate_discovered_tool(tool: dict[str, Any], limits: MCPResourceLimits) -> dict[str, Any]:
    name = tool.get("name")
    if not isinstance(name, str) or not name:
        raise MCPBoundaryError("MCP tool discovery entry requires a non-empty name")
    if len(name.encode("utf-8")) > limits.max_tool_name_bytes:
        raise MCPBoundaryError("MCP tool name exceeds Manager's byte limit")

    description = tool.get("description")
    if description is not None:
        if not isinstance(description, str):
            raise MCPBoundaryError("MCP tool description must be text when present")
        if len(description.encode("utf-8")) > limits.max_description_bytes:
            raise MCPBoundaryError(
                f"MCP tool {name!r} description exceeds Manager's byte limit"
            )

    schema = tool.get("input_schema")
    if not isinstance(schema, dict):
        raise MCPBoundaryError(f"MCP tool {name!r} is missing an input schema")
    bounded_schema = bound_json_value(
        schema,
        label=f"MCP tool {name!r} schema",
        max_bytes=limits.max_schema_bytes,
        max_depth=limits.max_schema_depth,
        max_items=limits.max_schema_items,
    )

    metadata = tool.get("remote_metadata")
    keys: list[str] = []
    if isinstance(metadata, dict):
        raw_keys = metadata.get("keys")
        if isinstance(raw_keys, list):
            keys = [str(key) for key in raw_keys[:64]]
        else:
            keys = [str(key) for key in list(metadata)[:64]]

    return {
        "name": name,
        "description": description,
        "input_schema": bounded_schema,
        "schema_fingerprint": remote_schema_fingerprint(bounded_schema),
        "remote_metadata": {"keys": sorted(keys)},
    }


def bound_mcp_result(value: Any, limits: MCPResourceLimits) -> Any:
    """Reject hostile tool results before they enter Manager state/continuation."""

    if hasattr(value, "model_dump"):
        value = value.model_dump(by_alias=True, exclude_none=True)
    return bound_json_value(
        value,
        label="MCP tool result",
        max_bytes=limits.max_result_bytes,
        max_depth=limits.max_result_depth,
        max_items=limits.max_result_items,
    )


def _address_allowed(address: IPAddress, policy: MCPNetworkPolicy) -> bool:
    if address.is_loopback:
        return policy.allow_loopback
    if address.is_link_local:
        return policy.allow_link_local
    if address.is_multicast:
        return policy.allow_multicast
    if address.is_unspecified:
        return policy.allow_unspecified
    if address.is_private:
        return policy.allow_private_networks
    # Shared, reserved, benchmarking, documentation, and other non-global
    # ranges are not safe public egress merely because `is_private` is false.
    # Treat them as internal by default.
    if not address.is_global:
        return policy.allow_private_networks
    return True


def _resolved_addresses(
    host: str,
    port: int,
    *,
    policy: MCPNetworkPolicy,
    limits: MCPResourceLimits,
) -> tuple[IPAddress, ...]:
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None

    if literal is not None:
        return (literal,)
    if not policy.resolve_hostnames:
        return ()

    try:
        records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise MCPBoundaryError("MCP HTTP target hostname could not be resolved") from exc

    addresses: list[IPAddress] = []
    seen: set[str] = set()
    for record in records:
        raw = str(record[4][0])
        if raw in seen:
            continue
        seen.add(raw)
        try:
            parsed = ipaddress.ip_address(raw)
        except ValueError as exc:
            raise MCPBoundaryError("MCP HTTP target resolved to an invalid address") from exc
        addresses.append(parsed)
        if len(addresses) > limits.max_resolved_addresses:
            raise MCPBoundaryError("MCP HTTP target resolves to too many addresses")
    if not addresses:
        raise MCPBoundaryError("MCP HTTP target hostname resolved to no addresses")
    return tuple(addresses)


def validate_http_target(
    url: str,
    *,
    policy: MCPNetworkPolicy,
    limits: MCPResourceLimits,
) -> None:
    """Apply Manager's pre-connect URL/SSRF policy.

    This is a defense-in-depth userspace check, not a firewall. Deployments that
    require strong network isolation must also enforce egress at the platform.
    """

    if not isinstance(url, str) or not url:
        raise MCPBoundaryError("MCP HTTP target must be a non-empty URL")
    if len(url.encode("utf-8")) > limits.max_url_bytes:
        raise MCPBoundaryError("MCP HTTP target exceeds Manager's URL byte limit")

    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise MCPBoundaryError("MCP HTTP target URL is invalid") from exc

    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise MCPBoundaryError("MCP HTTP target scheme must be http or https")
    if parsed.username is not None or parsed.password is not None:
        raise MCPBoundaryError("MCP HTTP target must not embed credentials")
    if parsed.fragment:
        raise MCPBoundaryError("MCP HTTP target must not contain a fragment")
    host = parsed.hostname
    if not host:
        raise MCPBoundaryError("MCP HTTP target requires a hostname")
    host = host.rstrip(".").lower()
    blocked_hosts = {item.rstrip(".").lower() for item in policy.blocked_hosts}
    if host in blocked_hosts:
        raise MCPBoundaryError("MCP HTTP target hostname is blocked by policy")
    if policy.allowed_hosts:
        allowed_hosts = {item.rstrip(".").lower() for item in policy.allowed_hosts}
        if host not in allowed_hosts:
            raise MCPBoundaryError("MCP HTTP target hostname is not allowlisted")

    effective_port = port or (443 if scheme == "https" else 80)
    addresses = _resolved_addresses(
        host,
        effective_port,
        policy=policy,
        limits=limits,
    )
    if addresses and any(not _address_allowed(item, policy) for item in addresses):
        raise MCPBoundaryError("MCP HTTP target resolves to an address blocked by policy")

    if scheme == "http":
        if addresses and all(item.is_loopback for item in addresses):
            if not policy.allow_plain_http_loopback:
                raise MCPBoundaryError("plain HTTP loopback MCP targets are disabled")
        elif addresses and all(item.is_private or not item.is_global for item in addresses):
            if not policy.allow_plain_http_private:
                raise MCPBoundaryError("plain HTTP private-network MCP targets are disabled")
        else:
            raise MCPBoundaryError(
                "plain HTTP MCP targets are only allowed by explicit local policy"
            )


def _text_bytes(value: str, *, label: str, maximum: int) -> None:
    if not isinstance(value, str) or not value:
        raise MCPBoundaryError(f"{label} must be non-empty text")
    if "\x00" in value:
        raise MCPBoundaryError(f"{label} must not contain NUL bytes")
    if len(value.encode("utf-8")) > maximum:
        raise MCPBoundaryError(f"{label} exceeds Manager's byte limit")


def _looks_like_shell_wrapper(command: str, args: list[str]) -> bool:
    base = Path(command).name.lower()
    if base in {"sh", "bash", "zsh", "fish"}:
        return any(arg in {"-c", "-lc"} for arg in args)
    if base in {"cmd", "cmd.exe"}:
        return any(arg.lower() in {"/c", "/k"} for arg in args)
    if base in {"powershell", "powershell.exe", "pwsh", "pwsh.exe"}:
        return any(arg.lower() in {"-command", "-c", "/c"} for arg in args)
    return False


def prepare_stdio_target(
    target: Any,
    *,
    policy: MCPStdioPolicy,
    limits: MCPResourceLimits,
) -> Any:
    """Validate and snapshot stdio launch parameters.

    The MCP SDK starts subprocesses without a shell and supplies only its
    allow-listed base environment plus explicitly configured entries. Manager
    additionally bounds command/args/env/cwd and refuses shell wrappers by
    default. Explicit MCP credentials may still be supplied by the embedding
    application, but Manager never copies arbitrary parent environment state.
    """

    command = getattr(target, "command", None)
    args = getattr(target, "args", None)
    if command is None or args is None:
        return target
    _text_bytes(command, label="MCP stdio command", maximum=limits.max_stdio_command_bytes)
    if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
        raise MCPBoundaryError("MCP stdio args must be a string array")
    if len(args) > limits.max_stdio_args:
        raise MCPBoundaryError("MCP stdio argument count exceeds Manager's limit")
    for index, arg in enumerate(args):
        if "\x00" in arg:
            raise MCPBoundaryError("MCP stdio arguments must not contain NUL bytes")
        if len(arg.encode("utf-8")) > limits.max_stdio_argument_bytes:
            raise MCPBoundaryError(
                f"MCP stdio argument {index} exceeds Manager's byte limit"
            )
    if not policy.allow_shell_wrappers and _looks_like_shell_wrapper(command, args):
        raise MCPBoundaryError("MCP stdio shell-wrapper execution is disabled by policy")

    env = getattr(target, "env", None)
    if env is not None:
        if not isinstance(env, Mapping):
            raise MCPBoundaryError("MCP stdio env must be a string mapping")
        if len(env) > limits.max_stdio_env_entries:
            raise MCPBoundaryError("MCP stdio env entry count exceeds Manager's limit")
        total = 0
        for key, value in env.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise MCPBoundaryError("MCP stdio env must contain only string keys and values")
            if not key or "\x00" in key or "=" in key or "\x00" in value:
                raise MCPBoundaryError("MCP stdio env contains an invalid entry")
            total += len(key.encode("utf-8")) + len(value.encode("utf-8"))
            if total > limits.max_stdio_env_bytes:
                raise MCPBoundaryError("MCP stdio env exceeds Manager's byte limit")

    cwd = getattr(target, "cwd", None)
    if cwd is not None:
        cwd_text = os.fspath(cwd)
        _text_bytes(cwd_text, label="MCP stdio cwd", maximum=limits.max_stdio_cwd_bytes)
        if policy.allowed_cwd_roots:
            resolved = Path(cwd_text).resolve()
            roots = [Path(root).resolve() for root in policy.allowed_cwd_roots]
            if not any(resolved == root or root in resolved.parents for root in roots):
                raise MCPBoundaryError("MCP stdio cwd is outside the configured allowed roots")

    if hasattr(target, "model_copy"):
        return target.model_copy(deep=True)
    try:
        return deepcopy(target)
    except Exception:
        try:
            return copy(target)
        except Exception as exc:
            raise MCPBoundaryError("MCP stdio target could not be snapshotted safely") from exc
