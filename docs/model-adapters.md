# Model provider adapters

Manager keeps model-provider behavior behind a narrow adapter boundary.

## Rule

A model adapter translates between Manager's provider-neutral model request/response contracts and a provider SDK. It does **not** own governance, task materiality, approval, tool authorization, state ownership, reconciliation, or readiness claims.

Deterministic governance runs before a provider call. A provider response is content produced inside an already-authorized workflow, not authority to widen that workflow.

## Canonical contracts

- [`model-request.schema.json`](../contracts/model-request.schema.json)
- [`model-response.schema.json`](../contracts/model-response.schema.json)

The v1 reference contract is text-only on purpose. Multimodal input, provider tools, structured output, streaming, and conversation state should be added only when a concrete cross-provider contract is justified.

## Data boundary

Provider calls are external data transfers. The reference orchestrator therefore:

1. blocks model calls when the deterministic control plane has blocked the task;
2. model-backs only the direct workflow in this stage;
3. sends only explicit `model_input` text, or the task objective when no model input is supplied;
4. does not forward retrieved/untrusted content automatically;
5. withholds non-public input by default unless the embedding application explicitly opts in.

Applications remain responsible for selecting a provider whose data-processing terms are appropriate for their deployment.

## OpenAI reference adapter

The first reference adapter uses OpenAI's Responses API through the optional official Python SDK dependency. Model selection is explicit at the call site; Manager does not hard-code a default model.

The adapter maps:

```text
Manager model request
→ OpenAI Responses API request
→ normalized Manager model response
```

Credentials come from the provider SDK's normal external configuration, such as environment variables. Credentials must never be committed to this public repository.

The runtime test suite uses an injected fake client. CI therefore verifies mapping and governance behavior without an API key, network request, or paid model invocation.

## Non-goals for this stage

This stage does not implement:

- provider-side or Manager-side tool calling;
- arbitrary external side effects;
- multimodal requests;
- streaming;
- durable conversation/session state;
- provider failover;
- automatic model selection;
- production-readiness claims.
