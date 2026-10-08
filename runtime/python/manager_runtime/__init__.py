"""Manager Python reference runtime.

This package is a reference implementation of the public Manager contracts.
It is not the canonical architecture definition and does not imply production readiness.
"""

from .agent_loop import run_bounded_agent_loop
from .capacity import CapacityLimits, CheckpointTooLarge, OverloadedError
from .engine import run
from .mcp import OfficialMCPClient, register_mcp_bindings
from .observability import InMemoryTelemetrySink, JsonLoggingSink, SafeTelemetry
from .operations import ObservedModelAdapter, ObservedRunStore, OperationalRuntime
from .orchestrator import run_with_model, run_with_model_and_tools
from .state import (
    resolve_recovery_required,
    resume_durable_agent_loop,
    run_durable_agent_loop,
)
from .tools import ToolRegistry, execute_tool_request

__all__ = [
    "run",
    "run_with_model",
    "run_with_model_and_tools",
    "run_bounded_agent_loop",
    "run_durable_agent_loop",
    "resume_durable_agent_loop",
    "resolve_recovery_required",
    "register_mcp_bindings",
    "OfficialMCPClient",
    "ToolRegistry",
    "execute_tool_request",
    "CapacityLimits",
    "CheckpointTooLarge",
    "OverloadedError",
    "SafeTelemetry",
    "InMemoryTelemetrySink",
    "JsonLoggingSink",
    "OperationalRuntime",
    "ObservedModelAdapter",
    "ObservedRunStore",
]
