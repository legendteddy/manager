# Python reference runtime

This directory contains Manager's first reference runtime.

The runtime remains intentionally narrow. It implements a deterministic **control plane** for routing, approval, bounded delegation, public-safety filtering, routine reconciliation, a provider-neutral model adapter boundary, and a governed custom-tool execution boundary. Python is the first reference implementation language; the canonical Manager contracts remain language- and provider-neutral.

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
- required verification for consequential tool classes;
- synthetic tool adapters and unit tests with no live side effects.

Not implemented:

- provider-executed built-in tools or provider-managed MCP execution;
- arbitrary production tools or credentials;
- model continuation turns after tool results;
- parallel tool execution;
- automatic retries of side effects;
- transaction rollback orchestration;
- sandbox/process isolation;
- durable state stores;
- external approval persistence;
- multimodal model input;
- streaming;
- durable provider conversation/session state;
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

The Stage 5 reference path offers only application-owned custom function definitions to the model. The provider may propose a function call, but the provider adapter does not execute it.

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

`run_with_model_and_tools` offers an explicit allowed tool set to the model and then routes returned proposals through the governed tool runtime. Analysis/read tools exposed by the embedding application are treated as in-scope for that model turn. Side-effecting tools still require their separate authorization, intent, target, approval, and verification gates.

Stage 5 intentionally does not send tool outputs back to the model for another response. That continuation loop is future work.

See [`docs/tool-runtime.md`](../../docs/tool-runtime.md) for the policy boundary.

## Governance boundary

`run_with_model` and `run_with_model_and_tools` execute deterministic governance before model or tool side effects. A tool proposal is never treated as authorization.

Non-public input is not sent to a provider by default. The embedding application must make an explicit opt-in decision before doing so, and remains responsible for its own provider/data-processing requirements.

A green test or eval run demonstrates only the behavior actually encoded and tested. It does not establish general reasoning quality, provider uptime, security completeness, private-reference parity, deployment readiness, or production suitability.
