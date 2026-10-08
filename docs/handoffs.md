# Bounded Handoffs

## Purpose

Delegation should be explicit enough that different agents, tools, or runtimes can cooperate without silently expanding authority.

## Handoff envelope

A substantive handoff should be able to represent:

```yaml
objective:
decision_context:
decision_owner:
decision_being_supported:
inputs:
scope:
exclusions:
authority:
output_contract:
completion_condition:
return_to:
```

## Rules

### Bound the task
Do not delegate vague instructions such as "research everything" or "give thoughts." Define the specific uncertainty, decision, or artifact.

### Preserve decision ownership
A handoff transfers work, not accountability, unless the packet explicitly assigns decision ownership within existing authority.

Contributors, specialists, critics, evaluators, risk reviewers, verifiers, and integrators do not silently become the domain decision owner.

### Avoid duplicated scope
Independent overlap is justified only when comparative judgment, adversarial challenge, or evaluator independence materially improves the outcome. Otherwise split work into non-overlapping scopes.

### Preserve provenance
When a handoff depends on external evidence, preserve enough provenance to verify material claims, including source, freshness when relevant, claim supported, and material uncertainty.

### Minimize context
Pass only the context necessary for the receiving capability to perform reliably. Prefer a concise decision packet over an entire conversation history.

### No silent authority expansion
A receiver may not silently widen:

```text
analyze → execute
draft → send
recommend → approve
inspect → mutate
evaluate → redefine domain truth
```

Tool permissions and approval requirements remain explicit.

## Return contract

Return to the smallest sufficient decision owner or coordinator when the assigned question is resolved, blocked, outside granted authority, or requires a material decision.

Do not add a coordinator hop merely to acknowledge completion.

## Failure return

When blocked, return:

- `blocked_on`
- `why_it_matters`
- `what_was_tried`
- `smallest_missing_input_or_permission`
- `safe_default_if_any`

Do not return a vague request for more information when the missing item can be identified precisely.
