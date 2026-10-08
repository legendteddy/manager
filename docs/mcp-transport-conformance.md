# MCP transport conformance

Stage 11 tests Manager's optional official MCP SDK bridge against a synthetic local MCP server over real stdio subprocess transport.

The purpose is narrow: prove that the protocol path Manager actually depends on behaves correctly without introducing a live external MCP service, production credentials, or real side effects.

## Tested path

```text
OfficialMCPClient
      ↓
official MCP Python SDK v2
      ↓
StdioServerParameters
      ↓
local synthetic subprocess
      ↓
MCP initialize / tools/list / tools/call
      ↓
normalized Manager MCP boundary
      ↓
ToolRegistry + Manager policy
```

The synthetic server is `runtime/python/tests/fixtures/synthetic_mcp_server.py`. It exposes only test tools and reads synthetic state from a temporary file created by the test process.

## Evidence covered in CI

The dedicated transport suite verifies:

- SDK negotiation and `tools/list` discovery over stdio;
- structured tool-result normalization through `OfficialMCPClient`;
- a discovered tool executing only after it has been bound into Manager's trusted `ToolRegistry`;
- remote schema drift after registration being detected immediately before execution, before the remote tool body runs;
- a remote tool disappearing after registration failing closed;
- the same adapter reconnecting successfully when the tool becomes available again;
- MCP `is_error` tool results being normalized into `MCPBoundaryError`;
- operation-level timeout cancelling a deliberately slow MCP call;
- stdio subprocess cleanup after cancellation;
- a later call reconnecting successfully after the timed-out process is gone;
- nested SDK task-group errors being normalized without leaking transport-specific exception-group behavior into Manager's public boundary.

The normal unit-test pass skips this transport suite when the optional MCP dependency is absent. CI then installs `runtime/python[mcp]` explicitly and runs the transport suite as a separate required step.

## Execution-time drift check

Registration-time schema validation is not sufficient for an external protocol. A server may change after registration.

Before each MCP-backed execution, `MCPToolAdapter` now re-discovers the selected remote tool and checks:

1. the configured logical server identity still matches;
2. the remote tool still exists exactly once;
3. the discovered input-schema fingerprint still matches the fingerprint reviewed at registration.

Any mismatch stops before `tools/call`.

This execution-time check is separate from durable checkpoint validation. Durable checkpoints additionally bind the effective Manager tool definition so a resumed consequential action also fails closed when MCP provenance changes.

## Timeout and cancellation semantics

`OfficialMCPClient` accepts an optional positive `operation_timeout_seconds` value.

The timeout covers the complete SDK operation, including connection setup, discovery required by the operation, the tool call, and SDK context cleanup. A timeout is surfaced as `MCPBoundaryError` rather than leaking AnyIO or SDK exception types through Manager's adapter boundary.

Each reference operation owns a fresh SDK client context. After cancellation or transport failure, the next operation reconnects rather than reusing an uncertain session.

This is deliberately conservative and not a connection-pooling design.

## What Stage 11 does not prove

The transport conformance suite does not establish:

- trustworthiness of arbitrary MCP servers;
- Streamable HTTP conformance;
- OAuth, credential, proxy, redirect, or certificate policy;
- long-lived connection pooling;
- cross-process session recovery;
- distributed MCP coordination;
- transport fuzzing or hostile-wire robustness;
- operating-system sandbox isolation;
- production latency or load characteristics;
- exactly-once remote effects;
- provider-managed MCP safety;
- production readiness.

MCP remains an interoperability boundary. Manager's local classification, authorization, approval, verification, redaction, durability, and recovery rules remain authoritative.
