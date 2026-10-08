# Model provider adapters

Manager keeps model-provider behavior behind a narrow adapter boundary.

## Rule

A model adapter translates between Manager's provider-neutral model request/response contracts and a provider SDK. It does **not** own governance, task materiality, approval, tool authorization, state ownership, reconciliation, loop budgets, or readiness claims.

Deterministic governance runs before a provider call. A provider response is content or a bounded tool proposal produced inside an already-authorized workflow, not authority to widen that workflow.

Provider outages, malformed responses, timeouts, capability differences, retries, and failover semantics are documented in [`provider-resilience.md`](provider-resilience.md).

## Canonical contracts

- [`model-request.schema.json`](../contracts/model-request.schema.json)
- [`model-response.schema.json`](../contracts/model-response.schema.json)
- [`tool-proposal.schema.json`](../contracts/tool-proposal.schema.json)
- [`agent-loop-policy.schema.json`](../contracts/agent-loop-policy.schema.json)

The reference model contract is text-first, supports optional custom tool definitions/proposals, and can carry a provider-neutral continuation envelope containing a prior response reference plus executed tool results. Multimodal input, streaming, and provider-executed tools should be added only when a concrete cross-provider contract preserves Manager's governance boundary.

Resilience metadata is additive under the existing `extensions.manager_runtime` object. Canonical runtime logic must not depend on one provider's SDK object shape, response-ID field name, token-accounting object, stop reason, or exception class.

## Capability declarations

Reference adapters may declare `ProviderCapabilities` for the Manager operations they can safely implement, including tools, structured output, continuation, streaming, request timeouts, context-window size, and a continuation-family identifier.

A capability declaration describes an adapter/model integration. It does not authorize a tool, widen task scope, or replace Manager policy. Legacy third-party adapters without declarations remain usable directly, while advanced resilience operations fail closed if required support is undeclared.

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

A provider proposal must carry stable provider identity when it will be used for continuation. The reference OpenAI adapter fails closed rather than inventing a call ID when the provider omits one.

Provider-executed built-in tools and provider-managed MCP tools remain outside the reference path because their execution can occur within the provider before Manager's local tool policy gate. Future support must preserve an equivalent enforceable boundary.

## OpenAI reference adapter

The OpenAI reference adapter uses the Responses API through the optional official Python SDK dependency. Model selection is explicit at the call site; Manager does not hard-code a default model.

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

The adapter declares tool, continuation, and per-request-timeout support. OpenAI SDK failures are normalized into Manager provider-error categories without copying raw remote/request messages into runtime artifacts. The default SDK client disables SDK retries so Manager's optional resilience wrapper can own visible retry accounting.

Credentials come from the provider SDK's normal external configuration, such as environment variables. Credentials must never be committed to this public repository.

The runtime test suite uses injected fake clients. CI therefore verifies mapping, malformed-output handling, continuation identity, timeout forwarding, and governance behavior without an API key, network request, paid model invocation, or live tool side effect.

## Synthetic reference adapter

`SyntheticModelAdapter` is a deterministic second implementation of the same provider-neutral boundary. It requires no credentials or network and can script successful responses, tool proposals, continuation, or normalized failure classes. CI uses it to prove that resilience behavior is not coupled to OpenAI object shapes.

## Resilient routing

`ResilientModelAdapter` composes one or more declared-capability adapters with finite retry and initial-failover policy. It never executes tools. Retry and failover therefore operate only on model calls after Manager has already established the tool/policy boundary.

Initial failover is allowed only for bounded transient, capability, or context incompatibility classes. Once a provider response establishes continuation identity, the route is pinned and cross-provider continuation is rejected. Durable resume must reconstruct the wrapper with the checkpointed provider identity pinned.

See [`provider-resilience.md`](provider-resilience.md) for the exact supported and unsupported semantics.

## Non-goals

The reference provider boundary does not implement or claim:

- provider-executed built-in tools;
- provider-managed MCP execution;
- cross-provider continuation or silent provider swapping mid-run;
- automatic model migration inside an existing durable provider conversation;
- arbitrary production tools or credentials;
- multimodal requests;
- streaming failover/resume;
- exactly-once provider calls;
- production-readiness or provider-SLA claims.
