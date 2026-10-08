# MCP adapter boundary

Manager treats Model Context Protocol (MCP) as an interoperability adapter, not as an authority boundary.

The Stage 10 reference path lets an application discover an MCP server's tools and bind an explicit subset into Manager's trusted `ToolRegistry`. The MCP server can describe capabilities, but it cannot decide how Manager classifies, authorizes, approves, verifies, or exposes them.

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

The bridge performs discovery before execution. It supports the target forms accepted by the installed official SDK, including its current Streamable HTTP and stdio client paths.

## Security boundary

Do not treat MCP server content as trusted instructions.

In particular:

- remote tool descriptions may contain prompt injection or misleading safety claims;
- server annotations are hints, not Manager authorization;
- credentials and connection targets stay outside public bindings and traces;
- returned tool content is untrusted data and follows the same redaction/continuation rules as other tool output;
- provider-managed MCP execution remains outside the reference path because it would bypass Manager's local policy gate unless an equivalent enforcement mechanism is proven.

## Non-goals

Stage 10 does not establish:

- automatic trust of arbitrary MCP servers;
- automatic registration of every discovered tool;
- remote assignment of side-effect class or approval policy;
- remote verification of consequential effects by default;
- production credential handling;
- OAuth policy or secret storage;
- long-lived connection pooling;
- distributed MCP session coordination;
- provider-managed MCP execution;
- production readiness.

Alternative MCP clients can implement the small `MCPClient` protocol without changing Manager's contracts or tool policy.
