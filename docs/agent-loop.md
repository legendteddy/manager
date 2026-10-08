# Bounded agent loop

## Purpose

Stage 7 adds a bounded multi-step reference path where verified tool results may be returned to a model for another decision turn.

The loop is intentionally controlled by Manager rather than by the model provider.

```text
model response
    ↓
custom tool proposals
    ↓
Manager policy + registry
    ↓
execute / block / require approval
    ↓
verification
    ↓
sanitize tool results
    ↓
provider-neutral continuation envelope
    ↓
next model turn
```

Every new tool proposal re-enters the same policy, authorization, approval, and verification boundary. A successful prior step does not grant authority to a later step.

## Budgets

The reference loop requires explicit finite budgets:

- `max_model_steps` limits model calls, including the initial call;
- `max_tool_calls` limits governed tool proposals processed by the loop;
- `max_tool_result_chars` bounds each serialized tool result sent back to the model.

If a model proposes a batch that would exceed the remaining tool-call budget, none of that over-budget batch is executed.

## Batch behavior

Multiple `analysis`/`read` proposals may be processed in one provider turn when they remain within budget.

If a multi-tool batch contains any consequential tool (`reversible_write`, `external_commitment`, or `sensitive_destructive`), the reference runtime blocks the batch before **any** tool in that batch executes. Consequential actions must be serialized into their own model turn so approval, target, verification, and interruption semantics remain unambiguous.

Unknown or unexposed tools also cause multi-tool batch preflight to stop before execution.

## Loop detection

Manager fingerprints tool name, target, and arguments. If the exact same tool action is proposed again within the same bounded loop, the reference runtime stops before executing it a second time.

This conservative rule avoids silent retry loops. Applications that need legitimate repeated actions should start a new explicitly bounded workflow or adopt a stricter domain-specific loop policy rather than weakening the default implicitly.

## Approval behavior

Approval is never carried forward automatically from one agent-loop action to another.

The Stage 7 loop deliberately removes any supplied `approval` object from reusable authorization context before evaluating a newly proposed action. If the action requires approval, the loop stops and returns the pending approval requirement.

Stage 6 durable approval checkpoints can resolve an individual consequential action, but Stage 7 does not yet persist and resume the entire multi-step model conversation around that interruption. Durable whole-loop resumption remains future work.

## Tool-result continuation

Only tool results with `status = executed` are eligible for model continuation.

Before continuation:

1. consequential execution must already have passed required verification;
2. output marked sensitive by the trusted tool registry is withheld from the model;
3. non-sensitive output is serialized and bounded by `max_tool_result_chars`;
4. the neutral continuation envelope identifies the prior model response and the proposal ID associated with each result.

The provider-neutral contract does not require one vendor's conversation mechanism. The OpenAI reference adapter maps the envelope to Responses API `previous_response_id` and `function_call_output` items.

## Stop conditions

The reference loop stops when:

- the model returns a final response with no tool proposals;
- the model-step budget is exhausted;
- the tool-call budget is exhausted;
- an exact tool proposal repeats;
- a consequential action appears inside a multi-tool provider batch;
- a multi-tool batch contains an unknown or unexposed tool;
- the provider does not return a usable continuation reference;
- a tool requires approval;
- policy blocks a tool;
- tool execution or verification fails;
- the model response is failed or incomplete.

Stopping is a control outcome, not evidence that the requested real-world objective was completed.

## Stage 7 limits

Stage 7 does not claim:

- durable resumption of the whole model/tool loop;
- exactly-once external side effects;
- parallel consequential tool execution;
- automatic side-effect retry;
- provider failover;
- distributed loop coordination;
- sandbox/process isolation;
- production credentials or production tool safety;
- private-reference parity;
- production readiness.

The loop is an executable reference control pattern, not a claim of unrestricted autonomous agency.
