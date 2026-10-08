# Model provider resilience

## Purpose

Manager treats model providers as fallible external capabilities, never as authority. Provider resilience exists to contain outages, malformed responses, rate limits, timeouts, capability differences, and provider/model changes without weakening Manager's deterministic policy, approval, tool, or durable-state controls.

The reference resilience layer is optional. Existing `ModelAdapter` implementations remain usable directly. Deployments that need normalized failures, bounded retries, explicit request timeouts, or initial-turn failover should wrap declared-capability adapters with `ResilientModelAdapter`.

## Provider contract

Canonical runtime logic consumes only Manager's normalized model request/response contracts. Provider-specific request fields, SDK objects, exception types, stop reasons, tool-call structures, and continuation mechanisms stay inside adapters.

Adapters may declare `ProviderCapabilities` for the Manager surface they implement:

- custom tools;
- structured output;
- continuation;
- streaming;
- request timeout support;
- context-window size when known;
- continuation-family identity.

Capabilities describe the adapter/model boundary used by Manager. They do not grant authority and do not imply that every upstream provider feature is enabled.

Legacy third-party adapters that do not declare capabilities remain compatible when called directly. The resilience wrapper fails closed before advanced capability-dependent operations when capability support is undeclared.

## Normalized failures

The Python reference layer normalizes provider failures into bounded categories:

| Category | Default retry | Initial failover | Meaning |
| --- | --- | --- | --- |
| `authentication_failure` | no | no | credentials are absent or rejected |
| `authorization_failure` | no | no | provider access is denied |
| `rate_limit` | yes | yes | provider throttled the request |
| `timeout` | yes | yes | model call exceeded its operation deadline |
| `transient_unavailable` | yes | yes | temporary transport/provider unavailability |
| `malformed_response` | no | no | provider output could not be trusted as the normalized contract |
| `unsupported_capability` | no same-provider retry | yes, initial turn only | route cannot support a required Manager capability |
| `context_limit` | no same-provider retry | yes, initial turn only | selected route cannot accept the request context |
| `provider_internal_error` | adapter-classified | only when marked retryable | bounded provider/internal failure |

Raw provider exception messages are not copied into Manager result artifacts. Exception chaining may remain available to trusted local diagnostics, but public/runtime-facing state carries only bounded categories and non-sensitive attempt metadata.

## Retries

Retries apply only to model-provider calls. They do not retry Manager tool execution.

`ProviderRetryPolicy` uses finite attempts and bounded exponential backoff. Same-provider retry is limited to failures explicitly classified retryable. Authentication, authorization, malformed output, unsupported capability, and context-limit failures are never retried against the same route by the resilience layer.

This separation matters after tool execution: a model continuation can be retried only as another model call using already checkpointed/sanitized tool results. Manager does not interpret a provider retry as permission to replay the external tool action that produced those results.

Provider SDKs injected by an embedding application may have their own retry policy. The default OpenAI client created by the reference adapter disables SDK retries so Manager's retry accounting remains visible. Applications that inject a preconfigured client are responsible for ensuring its hidden retry behavior is compatible with their budgets and observability requirements.

## Timeouts

Timeouts are explicit policy, not an implicit socket default. `ResilientModelAdapter` can inject a positive `timeout_seconds` value into Manager's reserved request extension. A capable provider adapter must enforce that deadline on its provider operation.

The OpenAI reference adapter maps the timeout to the Responses API SDK call. Synthetic tests cover timeout failures during initial generation, tool-proposal generation, and continuation/final-generation turns.

A timeout never proves whether a provider processed the request. Manager therefore treats the next step as another model-call decision only. It does not use provider timeout handling to infer or repeat an external tool side effect.

## Initial failover

Reference failover is deliberately narrow.

Before a provider response has established continuation identity, a configured route may fall through to another provider/model for:

- exhausted rate-limit retries;
- exhausted timeout retries;
- transient unavailability;
- retryable provider-internal failures;
- unsupported required capabilities;
- context-limit incompatibility.

Authentication/authorization failures do not silently switch providers because that could hide a deployment-policy or credential problem. Malformed provider output also does not fail over because accepting a different provider after an untrusted response can conceal a contract defect that should be investigated.

On the first successful response, the resilience wrapper pins that provider route for the logical conversation.

## Continuation and failover safety

Cross-provider continuation is **not supported** by the reference runtime.

A neutral `prior_response_ref` does not imply that two providers share conversation identity, tool-call identity, token accounting, hidden context, safety state, or response semantics. Once a provider has returned a response that may be continued, later turns stay on that provider route. If the pinned provider is unavailable, Manager returns a bounded failed model response rather than silently switching providers.

A fresh `ResilientModelAdapter` presented with a continuation request and no explicitly pinned provider fails closed before calling any provider. On process restart, the embedding application must reconstruct the resilience wrapper with the durable checkpoint's provider identity as `pinned_provider`.

Provider-route model overrides are useful for initial failover, but changing such an override across a durable restart is not proven compatible by checkpoint version 1. Deployments using durable continuation should keep the route/model mapping stable for the lifetime of the run. A future checkpoint revision should persist an explicit route/capability fingerprint before Manager claims safe migration across changed provider/model mappings.

## Durable state

Durable agent-loop checkpoint version 1 already persists:

- provider identity;
- requested model identity;
- consumed model/tool budgets;
- exact seen-action fingerprints;
- trusted tool-definition fingerprints;
- prior response reference;
- normalized current response or sanitized continuation results.

Resume rejects a different adapter provider identity and does not accept a new model argument. A resilience wrapper used for resume must therefore be pinned to the stored provider. Provider credentials are never persisted.

The resilience layer adds non-sensitive diagnostic metadata under `extensions.manager_runtime`, including selected provider/model, capability fingerprint, attempt count, whether initial failover occurred, and bounded attempt outcomes. This is diagnostic evidence, not authorization state.

## Malformed output handling

The reference adapters fail closed for provider behavior that makes authority or continuation identity uncertain, including:

- missing provider response identity;
- malformed function-call JSON;
- function proposals without stable call identity;
- invalid tool names/argument shapes;
- duplicate proposal identities;
- unexpected normalized fields or usage types;
- tool proposals attached to failed/incomplete responses;
- unsupported provider status or incomplete/stop reason;
- unexpected response/output types.

The OpenAI adapter no longer invents a synthetic call identity when the provider omits one. A fabricated ID would not be a trustworthy continuation key.

## Synthetic provider

`SyntheticModelAdapter` is the second complete reference implementation. It is deterministic, credential-free, network-free, and suitable for CI. A script can return normalized responses, raise normalized provider failures, or derive a response from the incoming request.

It exercises text generation, custom tool proposals, continuation, capability differences, retry/failover behavior, timeout/error handling, and stable provider/model identity without live provider calls.

## Unsupported semantics

The reference implementation does not claim:

- cross-provider continuation or mid-run provider swapping;
- automatic model migration within an existing durable provider conversation;
- exactly-once provider calls;
- live-provider credential or quota conformance in CI;
- streaming failover/resume;
- provider-managed tool execution;
- inference that a timeout means a provider request was not processed;
- automatic retry of consequential external tools;
- compatibility across changed route-model mappings after a durable restart;
- production availability/SLA guarantees.

These limits are intentional fail-closed boundaries, not invitations for adapters to guess.

## Failure-test inventory

The provider resilience suite exercises at least:

- authentication, authorization, rate-limit, timeout, unavailable, context-limit, and internal failure normalization;
- bounded same-provider retry;
- no retry/failover for authentication and malformed output;
- initial failover after transient/capability/context failures;
- capability-aware route selection;
- no cross-provider failover after continuation identity is pinned;
- fresh-process continuation without a pin failing before provider contact;
- explicit restart pinning;
- OpenAI request timeout forwarding;
- missing response/tool-call identity;
- malformed JSON tool arguments;
- unsupported response and incomplete reasons;
- sensitive provider exception text exclusion;
- deterministic synthetic initial/tool/continuation behavior.

## Residual risks

Provider APIs and SDK exception taxonomies evolve. The OpenAI mapping intentionally uses a small set of stable status/code/type signals and otherwise falls back to a bounded internal-error category. New upstream failure classes may therefore be conservative until explicitly mapped and tested.

Checkpoint version 1 identifies provider and requested model but does not canonically persist a resilience-route/capability fingerprint. Mid-run provider swapping is forbidden, and durable deployments should keep route/model/capability mappings stable. If Manager later supports deliberate provider/model migration, that requires a versioned checkpoint contract and reviewed migration semantics rather than best-effort fallback.
