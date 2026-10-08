# Python reference runtime

This directory contains Manager's first reference runtime.

The runtime remains intentionally narrow. It implements a deterministic **control plane** for routing, approval, bounded delegation, public-safety filtering, routine reconciliation, a provider-neutral model adapter boundary, a governed custom-tool execution boundary, a Manager-owned MCP adapter boundary, durable approval checkpoints, a bounded multi-step model/tool continuation loop, a durable resumable loop state machine, persisted-state conformance checks, explicit recovery resolution, and an end-to-end synthetic MCP stdio transport test path. Python is the first reference implementation language; the canonical Manager contracts remain language- and provider-neutral.

## Scope

Implemented:

- direct routing for low-risk routine tasks;
- material-action approval blocking;
- stale-approval rejection;
- authority-preserving specialist handoffs;
- routine authority-first reconciliation;
- prompt-injection resistance at the control layer;
- public/private exclusion decisions for modeled public writes;
- provider-neutral model adapter protocol;
- OpenAI Responses API reference adapter;
- model-backed execution for eligible direct public tasks;
- non-public provider input withheld by default;
- trusted tool registry with registry-owned side-effect metadata;
- model custom-function proposals normalized without provider execution;
- deterministic tool allow/block/approval decisions;
- explicit scope authorization for non-analysis tools;
- external-commitment human-intent and target-verification gates;
- exact approval fingerprints for sensitive/destructive tools;
- stale tool-approval rejection after target or argument changes;
- application-owned versioning for consequential tool definitions;
- required verification for consequential tool classes;
- provider-neutral `MCPClient` protocol;
- explicit allowlisted MCP tool bindings into `ToolRegistry`;
- remote MCP descriptions/annotations excluded from trusted policy metadata;
- exact remote input-schema matching before MCP registration;
- execution-time MCP server/tool/schema revalidation before every remote call;
- MCP server/tool/schema provenance bound into effective registered tool version;
- application-owned verifier requirement for consequential MCP tools;
- optional official MCP Python SDK v2 bridge;
- normalized SDK task-group errors and optional operation-level timeouts;
- official-SDK stdio subprocess conformance tests for discovery, governed execution, result normalization, schema drift, disappearance/reconnect, error results, timeout cancellation, and post-timeout reconnect;
- SQLite durable run checkpoints using the Python standard library;
- optimistic revision checks for state updates;
- persisted-state shape validation and legal transition enforcement;
- malformed/corrupted persisted-state rejection on load;
- persisted approval interruptions that survive process restart;
- current authorization, target, request, and tool-definition revalidation on resume;
- `recovery_required` fail-closed handling after interrupted execution intent;
- explicit `resolve_recovery_required(...)` handling from external evidence;
- fresh approval identity after externally confirmed non-execution;
- recovered durable-loop continuation after externally confirmed success without re-executing the tool;
- explicit checkpoint-version migration boundary with fail-closed future-version handling;
- bounded multi-step model/tool continuation through `run_bounded_agent_loop`;
- finite model-step, tool-call, and tool-result-size budgets;
- exact repeated-tool loop detection before re-execution;
- no automatic approval carry-forward between loop actions;
- sensitive tool-result withholding before model continuation;
- OpenAI continuation through `previous_response_id` plus `function_call_output`;
- durable multi-step execution through `run_durable_agent_loop` and `resume_durable_agent_loop`;
- persisted model/tool phases that preserve consumed budgets and seen-action fingerprints across restart;
- provider and allowed-tool-definition revalidation before durable continuation;
- mandatory durable approval checkpoints for every consequential tool class in durable mode;
- process-restart continuation after an approved, verified side effect without replaying that side effect;
- full Draft 2020-12 schema conformance testing for representative emitted runtime artifacts;
- synthetic tool adapters and unit tests with no live external side effects.

Not implemented:

- provider-executed built-in tools or provider-managed MCP execution;
- automatic trust or registration of arbitrary MCP servers/tools;
- production MCP credentials, OAuth policy, or secret storage;
- Streamable HTTP transport conformance;
- long-lived MCP connection pooling or distributed MCP session coordination;
- arbitrary production tools or credentials;
- exactly-once provider calls;
- exactly-once external side effects;
- parallel consequential tool execution;
- automatic retries of side effects;
- transaction rollback orchestration;
- sandbox/process isolation;
- encrypted state-at-rest management;
- distributed locks, leases, or high-availability state stores;
- automatic reconciliation with arbitrary external systems;
- multimodal model input;
- streaming;
- provider failover or automatic model selection;
- production readiness or private-reference parity.

## Run deterministic evals

From the repository root:

```bash
PYTHONPATH=runtime/python python3 -m manager_runtime.evals evals/cases
```

Run unit tests:

```bash
PYTHONPATH=runtime/python python3 -m unittest discover -s runtime/python/tests -v
```

Run full contract conformance:

```bash
python3 -m pip install -e 'runtime/python[conformance]'
PYTHONPATH=runtime/python python3 scripts/schema_conformance.py
```

The conformance dependency is test-only. Normal reference-runtime execution remains zero-dependency unless an optional provider or protocol extra is installed.

## OpenAI reference adapter

Install the optional provider dependency:

```bash
python3 -m pip install -e 'runtime/python[openai]'
```

The adapter uses the official Python SDK's Responses API. Credentials are supplied through the SDK's normal external configuration, such as `OPENAI_API_KEY`; never commit credentials to this repository.

Model selection is explicit. Manager deliberately does not hard-code a default model.

## MCP reference adapter

Install the optional MCP dependency:

```bash
python3 -m pip install -e 'runtime/python[mcp]'
```

The reference extra currently targets the official MCP Python SDK v2 line with `mcp>=2.2,<3`.

`OfficialMCPClient` adapts SDK discovery and tool calls to Manager's small synchronous `MCPClient` protocol. Connection targets and credentials are supplied by the embedding application and are not part of canonical Manager contracts.

MCP discovery does not grant authority. Use `register_mcp_bindings(...)` with explicit local bindings. Only configured tools are registered, the discovered input schema must match the reviewed local schema exactly, and remote descriptions or annotations never become Manager policy automatically.

Before an MCP-backed tool call, the adapter re-discovers the selected tool and verifies server identity, exact tool presence, and the reviewed input-schema fingerprint. Drift fails before remote execution.

Consequential MCP tools must provide an application-owned verifier. A successful MCP tool response alone is not treated as independent verification of a real-world side effect.

`OfficialMCPClient` also accepts an optional positive `operation_timeout_seconds`. Timeout, transport, nested task-group, and MCP error-result failures are normalized into `MCPBoundaryError`.

Run the dedicated stdio transport suite with:

```bash
PYTHONPATH=runtime/python python3 -m unittest discover -s runtime/python/tests -p 'test_mcp_transport_conformance.py' -v
```

The suite launches only a synthetic local subprocess and uses no external network service or credential. See [`docs/mcp-adapters.md`](../../docs/mcp-adapters.md) and [`docs/mcp-transport-conformance.md`](../../docs/mcp-transport-conformance.md).

## Governed custom tools

The reference path offers only application-owned custom function definitions to the model. The provider may propose a function call, but the provider adapter does not execute it.

```text
model proposal
→ Manager ToolRequest
→ trusted ToolRegistry metadata
→ deterministic policy / approval
→ native or Manager-owned MCP adapter execution when allowed
→ verification when required
→ ToolResult + trace event
```

Use `ToolRegistry` to register tool metadata and implementations. The model never controls a tool's side-effect class or trusted authorization context.

`run_with_model_and_tools` remains the one-round reference path. `run_bounded_agent_loop` adds bounded continuation after successfully executed and verified tool results.

## Bounded agent loop

The non-durable loop is intentionally finite and conservative:

```text
model
→ proposal
→ policy
→ tool
→ verification
→ sanitized result
→ continuation
→ model
```

Default reference budgets are four model steps, eight tool calls, and 8,000 characters per serialized tool result. Callers may choose smaller or larger finite values.

Exact repeated tool name + target + arguments stop the loop before a second execution. A proposal batch that exceeds the remaining tool-call budget is not partially executed. Approval objects in reusable authorization context are discarded before each new action so prior approval cannot silently authorize a later step.

Sensitive tool output is replaced with a policy message before model continuation. Non-sensitive output is serialized and truncated to the configured size bound if necessary.

See [`docs/agent-loop.md`](../../docs/agent-loop.md).

## Durable approval state

`SQLiteRunStore` provides the first durable run-state adapter. The database file location is supplied by the embedding application and must live outside this public repository.

A pending consequential action can be checkpointed with `checkpoint_pending_tool_approval(...)`, then resumed later with `resume_tool_approval(...)` after an explicit human approval decision.

On resume, Manager revalidates:

- approval freshness and ID;
- tool name, target, and arguments;
- current registered tool definition and version;
- current scope authorization;
- current target verification where required.

If execution was durably marked `executing` and the process disappeared before recording the outcome, a later resume changes the run to `recovery_required` rather than automatically repeating the action.

See [`docs/run-state.md`](../../docs/run-state.md).

## Durable bounded agent loop

The durable loop connects the durable state layer to the bounded loop:

```text
run_durable_agent_loop(...)
→ persist response_ready
→ evaluate proposal
→ read/analysis result → persist continuation_ready
→ consequential proposal → persist waiting_approval
→ human decision later
→ resume_durable_agent_loop(...)
→ revalidate provider + tools + request + authorization
→ persist executing
→ execute + verify
→ persist continuation_ready
→ continue model loop with original budgets and seen-action history
```

Durable mode is stricter than the non-durable loop. `reversible_write`, `external_commitment`, and `sensitive_destructive` actions always require a durable approval checkpoint before execution, even when the lower-level tool policy might otherwise permit a routine reversible write.

Restart does not reset model/tool budgets or exact repeated-action detection. The checkpoint also binds the provider, model, allowed tool set, and trusted tool-definition fingerprints. MCP-backed tools additionally include binding provenance in their effective registered version, so configured server/tool/schema drift invalidates the durable definition fingerprint.

A provider call can still be reissued if a process fails after the provider responds but before the next local checkpoint. Manager therefore does not claim exactly-once provider calls.

See [`docs/durable-agent-loop.md`](../../docs/durable-agent-loop.md).

## Recovery resolution

`resolve_recovery_required(...)` never executes an uncertain tool action merely because the run restarted.

- `confirmed_succeeded` records external evidence and a recovered verified result. A durable loop returns to `continuation_ready` without re-executing the tool.
- `confirmed_not_executed` creates a new request ID and approval ID and returns to `waiting_approval` after current authorization is re-established.
- `cancelled` terminates without another execution.

See [`docs/conformance-recovery.md`](../../docs/conformance-recovery.md).

## Governance boundary

Model, MCP discovery metadata, and tool proposals remain outside the authority boundary. Durable state does not make an old approval permanently valid, and a bounded loop does not make a previous approval reusable.

The SQLite reference adapter is a durability proof, not an encryption layer or universal production datastore recommendation. Embedding applications remain responsible for access control, encryption, retention, backup, and regulatory requirements appropriate to their environment.

A green test, eval, schema-conformance run, or synthetic MCP transport run demonstrates only the behavior and artifacts actually encoded and tested. It does not establish general reasoning quality, provider uptime, arbitrary MCP-server trustworthiness, Streamable HTTP conformance, security completeness, distributed exactly-once execution, private-reference parity, deployment readiness, or production suitability.
