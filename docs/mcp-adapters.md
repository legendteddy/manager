# MCP adapter boundary

Manager treats Model Context Protocol (MCP) as an interoperability adapter, not as an authority boundary.

The reference path lets an application discover an MCP server's tools and bind an explicit subset into Manager's trusted `ToolRegistry`. The MCP server can describe capabilities, but it cannot decide how Manager classifies, authorizes, approves, verifies, or exposes them.

## Trust model

MCP discovery is untrusted capability metadata.

A discovered tool is registered only when an application-owned binding already declares:

- a logical `server_id`;
- the exact remote MCP tool name;
- a local Manager tool name;
- a local model-facing description;
- the expected input schema;
- side-effect class;
- verification requirement;
- sensitive-output policy;
- an application-owned behavior version when consequential.

Manager does **not** automatically trust or promote remote descriptions, titles, annotations, read-only/destructive hints, output declarations, or other server metadata into local governance fields.

## Discovery and registration

```text
configured MCP target
      ↓
MCP tools/list discovery
      ↓
untrusted remote tool metadata
      ↓
exact local binding lookup
      ↓
server identity + tool name + input schema match
      ↓
Manager-owned policy metadata
      ↓
trusted ToolRegistry registration
```

Unconfigured discovered tools are ignored.

If a configured remote tool disappears, duplicates a name, or changes its input schema, registration fails closed and requires local review.

The effective registered tool version incorporates the configured server identity, remote tool name, and discovered schema fingerprint. Durable checkpoints therefore detect MCP binding drift before resumed consequential execution.

## Execution-time revalidation

Registration does not permanently trust a remote capability.

Immediately before an MCP-backed tool executes, the reference adapter re-discovers the selected remote tool and verifies:

1. the logical server identity still matches the configured binding;
2. the remote tool still exists exactly once;
3. its input-schema fingerprint still matches the fingerprint reviewed at registration.

A mismatch stops before `tools/call`. This closes the gap where an MCP server changes after registration but before execution.

## Execution

After registration, MCP-backed tools use the same governed tool runtime as native adapters:

```text
model / primary agent / human proposal
      ↓
Manager ToolRequest
      ↓
trusted ToolRegistry metadata
      ↓
argument validation
      ↓
scope / intent / target / approval policy
      ↓
MCP identity + schema revalidation
      ↓
MCP call only when allowed
      ↓
application-owned verification when required
      ↓
Manager ToolResult
```

A model never calls the MCP server directly through this reference path.

For consequential MCP tools, registration requires an application-owned verifier. A successful MCP response alone is not independent proof that the intended external effect occurred.

## Official Python SDK bridge

`OfficialMCPClient` is an optional reference bridge to the official MCP Python SDK v2 line. The runtime extra is:

```bash
python3 -m pip install -e 'runtime/python[mcp]'
```

The current reference constraint is `mcp>=2.2,<3`.

The SDK target is supplied by the embedding application and remains outside Manager's canonical contracts. This keeps URLs, process commands, credentials, OAuth configuration, and other environment-specific connection material out of the public architecture boundary.

The bridge performs discovery before execution and accepts an optional positive `operation_timeout_seconds`. SDK transport errors, nested task-group failures, MCP error results, and configured operation timeouts are normalized into Manager's `MCPBoundaryError` boundary.

Each reference operation owns a fresh SDK client context. This favors fail-closed cleanup and reconnectability over connection pooling.

## Stage 11 transport evidence

CI now installs the optional MCP dependency and launches a synthetic MCP server as a real stdio subprocess. The transport-conformance suite proves:

- official SDK discovery over stdio;
- structured result normalization;
- governed execution through `ToolRegistry`;
- schema drift blocking after registration and before remote execution;
- disappearance and later reconnect behavior;
- MCP error-result normalization;
- slow-call timeout/cancellation and subprocess cleanup;
- successful reconnection after a timed-out call.

No external MCP service, production credential, or real side effect is used. See [`mcp-transport-conformance.md`](mcp-transport-conformance.md).

## Security boundary

Do not treat MCP server content as trusted instructions.

In particular:

- remote tool descriptions may contain prompt injection or misleading safety claims;
- server annotations are hints, not Manager authorization;
- credentials and connection targets stay outside public bindings and traces;
- returned tool content is untrusted data and follows the same redaction/continuation rules as other tool output;
- provider-managed MCP execution remains outside the reference path because it would bypass Manager's local policy gate unless an equivalent enforcement mechanism is proven.

## Non-goals

The current MCP path does not establish:

- automatic trust of arbitrary MCP servers;
- automatic registration of every discovered tool;
- remote assignment of side-effect class or approval policy;
- remote verification of consequential effects by default;
- production credential handling;
- OAuth policy or secret storage;
- Streamable HTTP conformance;
- long-lived connection pooling;
- distributed MCP session coordination;
- provider-managed MCP execution;
- production readiness.

Alternative MCP clients can implement the small `MCPClient` protocol without changing Manager's contracts or tool policy.
