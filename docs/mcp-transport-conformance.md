# MCP transport conformance

Manager tests its optional official MCP SDK bridge against synthetic local servers over the transport paths the reference runtime actually supports.

The purpose is narrow: prove protocol and adapter behavior without introducing a live external MCP service, production credentials, or real side effects.

## Tested transport paths

### Stage 11: stdio

```text
OfficialMCPClient
      ↓
official MCP Python SDK v2
      ↓
StdioServerParameters
      ↓
local synthetic subprocess
      ↓
MCP discovery / tools/list / tools/call
      ↓
normalized Manager MCP boundary
      ↓
ToolRegistry + Manager policy
```

The synthetic stdio server is `runtime/python/tests/fixtures/synthetic_mcp_server.py`.

### Stage 12: Streamable HTTP

```text
OfficialMCPClient
      ↓
URL target
      ↓
official MCP Python SDK v2 Streamable HTTP client
      ↓
loopback HTTP
      ↓
local synthetic ASGI MCP server
      ↓
MCP discovery / tools/list / tools/call
      ↓
normalized Manager MCP boundary
      ↓
ToolRegistry + Manager policy
```

The synthetic HTTP server is `runtime/python/tests/fixtures/synthetic_mcp_http_server.py`. CI launches it on a temporary loopback port with no external network dependency.

## Evidence covered in CI

### Stdio suite

The dedicated stdio transport suite verifies:

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

### Streamable HTTP suite

The dedicated Streamable HTTP suite verifies:

- official SDK discovery and structured result normalization through a URL target;
- a real HTTP MCP tool call executing through Manager's `ToolRegistry` and policy runtime;
- remote input-schema drift after registration failing before the HTTP `tools/call` body executes;
- server unavailability failing closed and the existing binding working again after the local server restarts on the same endpoint;
- MCP error results being normalized through Manager's boundary;
- operation timeout cancelling a deliberately slow HTTP tool call;
- a later operation reconnecting successfully after the cancelled request;
- a method-preserving `307` redirect being followed when it remains on the same origin;
- a cross-origin `307` redirect being rejected and the SDK's redirect reason being preserved through `MCPBoundaryError`;
- a synthetic custom HTTP header supplied by an embedding application through the SDK transport reaching the MCP handler without becoming part of Manager's canonical contracts;
- malformed JSON HTTP responses failing closed rather than being interpreted as valid MCP data.

The normal base unit-test pass skips transport tests when the optional MCP dependency is absent. CI installs `runtime/python[mcp]` explicitly, then runs the stdio and Streamable HTTP suites as separate required steps.

## Execution-time drift check

Registration-time schema validation is not sufficient for an external protocol. A server may change after registration.

Before each MCP-backed execution, `MCPToolAdapter` re-discovers the selected remote tool and checks:

1. the configured logical server identity still matches;
2. the remote tool still exists exactly once;
3. the discovered input-schema fingerprint still matches the fingerprint reviewed at registration.

Any mismatch stops before `tools/call`.

This execution-time check is transport-independent. The same invariant is exercised over both stdio and Streamable HTTP.

Durable checkpoints additionally bind the effective Manager tool definition so a resumed consequential action also fails closed when MCP provenance changes.

## Timeout and cancellation semantics

`OfficialMCPClient` accepts an optional positive `operation_timeout_seconds` value.

The timeout covers the complete SDK operation, including connection setup, discovery required by the operation, the tool call, and SDK context cleanup. A timeout is surfaced as `MCPBoundaryError` rather than leaking AnyIO or SDK exception types through Manager's adapter boundary.

Each reference operation owns a fresh SDK client context. After cancellation or transport failure, the next operation reconnects rather than reusing an uncertain session.

This is deliberately conservative and not a connection-pooling design.

## HTTP redirect boundary

The reference client relies on the official SDK's Streamable HTTP redirect policy.

The current conformance suite proves two cases:

- a method-preserving redirect that remains on the same scheme, host, and port can be followed;
- a redirect to a different origin is rejected rather than silently carrying the MCP request elsewhere.

The rejected redirect's reason is normalized through Manager's `MCPBoundaryError` boundary so operators can distinguish a deliberate transport-policy refusal from a generic connection failure.

This evidence is limited to the tested SDK line and synthetic local endpoints. It is not a substitute for deployment-specific egress controls.

## HTTP headers and credentials

Manager does not place headers, tokens, cookies, OAuth configuration, proxy configuration, or certificates into canonical MCP bindings.

The Stage 12 suite demonstrates that an embedding application can configure a synthetic custom header through the official SDK's HTTP client and that the header reaches the MCP handler. That proves the integration point, not a production authentication policy.

Real credentials remain outside this public repository and outside canonical Manager contracts.

## Malformed HTTP responses

A synthetic HTTP endpoint returns malformed JSON while claiming an HTTP JSON response. The official transport rejects it and Manager surfaces a boundary failure.

Manager therefore does not treat a successful TCP/HTTP exchange as sufficient evidence of a valid MCP response.

## What the transport suites do not prove

The current conformance evidence does not establish:

- trustworthiness of arbitrary MCP servers;
- production OAuth or credential handling;
- token refresh or authorization-server behavior;
- TLS certificate, mTLS, proxy, or enterprise egress policy;
- HTTP-to-HTTPS redirect policy beyond the SDK's tested implementation;
- request-scoped SSE response streaming behavior;
- long-lived connection pooling;
- cross-process HTTP session recovery;
- distributed MCP coordination;
- transport fuzzing or hostile-wire robustness beyond the malformed-response case;
- operating-system sandbox isolation;
- production latency or load characteristics;
- exactly-once remote effects;
- provider-managed MCP safety;
- production readiness.

MCP remains an interoperability boundary. Manager's local classification, authorization, approval, verification, redaction, durability, and recovery rules remain authoritative.
