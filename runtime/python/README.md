# Python reference runtime

This directory contains Manager's first reference runtime.

The runtime remains intentionally narrow. It implements a deterministic **control plane** for routing, approval, bounded delegation, public-safety filtering, routine reconciliation, a provider-neutral model adapter boundary, a governed custom-tool execution boundary, durable approval checkpoints, and a bounded multi-step model/tool continuation loop. Python is the first reference implementation language; the canonical Manager contracts remain language- and provider-neutral.

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
- SQLite durable run checkpoints using the Python standard library;
- optimistic revision checks for state updates;
- persisted approval interruptions that survive process restart;
- current authorization, target, request, and tool-definition revalidation on resume;
- `recovery_required` fail-closed handling after interrupted execution intent;
- bounded multi-step model/tool continuation through `run_bounded_agent_loop`;
- finite model-step, tool-call, and tool-result-size budgets;
- exact repeated-tool loop detection before re-execution;
- no automatic approval carry-forward between loop actions;
- sensitive tool-result withholding before model continuation;
- OpenAI continuation through `previous_response_id` plus `function_call_output`;
- synthetic tool adapters and unit tests with no live side effects.

Not implemented:

- provider-executed built-in tools or provider-managed MCP execution;
- arbitrary production tools or credentials;
- durable resumption of the whole multi-step model/tool loop;
- parallel tool execution;
- automatic retries of side effects;
- exactly-once external side effects;
- transaction rollback orchestration;
- sandbox/process isolation;
- encrypted state-at-rest management;
- distributed locks or high-availability state stores;
- durable provider conversation/session state beyond adapter-level continuation references;
- multimodal model input;
- streaming;
- provider failover or automatic model selection;
- full JSON Schema validation;
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

## OpenAI reference adapter

Install the optional provider dependency:

```bash
python3 -m pip install -e 'runtime/python[openai]'
```

The adapter uses the official Python SDK's Responses API. Credentials are supplied through the SDK's normal external configuration, such as `OPENAI_API_KEY`; never commit credentials to this repository.

Model selection is explicit. Manager deliberately does not hard-code a default model.

## Governed custom tools

The reference path offers only application-owned custom function definitions to the model. The provider may propose a function call, but the provider adapter does not execute it.

```text
model proposal
→ Manager ToolRequest
→ trusted ToolRegistry metadata
→ deterministic policy / approval
→ adapter execution when allowed
→ verification when required
→ ToolResult + trace event
```

Use `ToolRegistry` to register tool metadata and implementations. The model never controls a tool's side-effect class or trusted authorization context.

`run_with_model_and_tools` remains the one-round reference path. `run_bounded_agent_loop` adds bounded continuation after successfully executed and verified tool results.

## Bounded agent loop

The Stage 7 loop is intentionally finite and conservative:

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

Stage 7 does not yet persist the surrounding multi-step model loop across that approval interruption.

See [`docs/run-state.md`](../../docs/run-state.md).

## Governance boundary

Model and tool proposals remain outside the authority boundary. Durable state does not make an old approval permanently valid, and a bounded loop does not make a previous approval reusable.

The SQLite reference adapter is a durability proof, not an encryption layer or universal production datastore recommendation. Embedding applications remain responsible for access control, encryption, retention, backup, and regulatory requirements appropriate to their environment.

A green test or eval run demonstrates only the behavior actually encoded and tested. It does not establish general reasoning quality, provider uptime, security completeness, private-reference parity, deployment readiness, or production suitability.
