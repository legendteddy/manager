# Model provider adapters

Manager keeps model-provider behavior behind a narrow adapter boundary.

## Rule

A model adapter translates between Manager's provider-neutral model request/response contracts and a provider SDK. It does **not** own governance, task materiality, approval, tool authorization, state ownership, reconciliation, loop budgets, or readiness claims.

Deterministic governance runs before a provider call. A provider response is content or a bounded tool proposal produced inside an already-authorized workflow, not authority to widen that workflow.

## Canonical contracts

- [`model-request.schema.json`](../contracts/model-request.schema.json)
- [`model-response.schema.json`](../contracts/model-response.schema.json)
- [`tool-proposal.schema.json`](../contracts/tool-proposal.schema.json)
- [`agent-loop-policy.schema.json`](../contracts/agent-loop-policy.schema.json)

The reference model contract is text-first, supports optional custom tool definitions/proposals, and can carry a provider-neutral continuation envelope containing a prior response reference plus executed tool results. Multimodal input, streaming, and provider-executed tools should be added only when a concrete cross-provider contract preserves Manager's governance boundary.

## Data boundary

Provider calls are external data transfers. The reference orchestrator therefore:

1. blocks model calls when the deterministic control plane has blocked the task;
2. model-backs only the direct workflow in this stage;
3. sends only explicit `model_input` text, or the task objective when no model input is supplied;
4. does not forward retrieved/untrusted content automatically;
5. withholds non-public input by default unless the embedding application explicitly opts in;
6. exposes only an explicit allowed custom-tool set chosen by the embedding application;
7. returns only executed, policy-eligible tool results to a continuation turn;
8. withholds outputs marked sensitive by trusted tool metadata and bounds non-sensitive serialized results before provider transfer.

Applications remain responsible for selecting a provider whose data-processing terms are appropriate for their deployment.

## Tool proposals and continuation

A provider may return custom function calls. The adapter normalizes those calls into Manager tool proposals containing only the proposed tool identity, arguments, optional target, and source reference.

The adapter never converts a provider function call directly into a side effect. Manager's governed tool runtime resolves the proposal against trusted registry metadata and separately evaluates authorization, approval, target verification, execution, and post-effect verification.

When a bounded loop continues, the neutral request includes:

- `prior_response_ref`: reference to the immediately prior model response;
- `tool_results`: executed result items keyed to the original proposal IDs.

The provider adapter may map this envelope to native conversation/tool-result primitives. Manager still owns the budgets, loop detection, policy checks, result sanitization, and stop conditions.

Provider-executed built-in tools and provider-managed MCP tools remain outside the reference path because their execution can occur within the provider before Manager's local tool policy gate. Future support must preserve an equivalent enforceable boundary.

## OpenAI reference adapter

The first reference adapter uses OpenAI's Responses API through the optional official Python SDK dependency. Model selection is explicit at the call site; Manager does not hard-code a default model.

The adapter maps initial turns as:

```text
Manager model request
→ OpenAI Responses API request
→ normalized text + custom function proposals
→ Manager policy / tool runtime
```

For continuation, the adapter maps Manager's neutral envelope to:

```text
prior_response_ref
→ previous_response_id

tool result proposal_id + output
→ function_call_output call_id + output
```

Only custom `function` definitions are emitted by the reference adapter. Function-call arguments are parsed into neutral proposals and are not executed by the provider adapter.

Credentials come from the provider SDK's normal external configuration, such as environment variables. Credentials must never be committed to this public repository.

The runtime test suite uses injected fake clients. CI therefore verifies initial-call mapping, continuation mapping, and governance behavior without an API key, network request, paid model invocation, or live tool side effect.

## Non-goals for this stage

This stage does not implement:

- provider-executed built-in tools;
- provider-managed MCP execution;
- durable resumption of an entire multi-step model/tool conversation across approval interruption;
- arbitrary production tools or credentials;
- multimodal requests;
- streaming;
- durable provider conversation/session state independent of returned response references;
- provider failover;
- automatic model selection;
- production-readiness claims.
